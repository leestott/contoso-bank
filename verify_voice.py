"""Verify a requested native voice turn, not an unrelated automatic greeting."""

import base64
import argparse
import json
import time
import uuid

from foundry import Foundry, FoundryError
from settings import load_config

MARKER = "foundry_voice_smoke_test_request"


def verify(locale="en-GB"):
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("Install requirements-voice.txt to verify native voice.") from exc
    config = load_config("banking")
    if locale != "en-GB":
        profile = config.get("voice_profiles", {}).get(locale)
        if not profile or not profile["version"]:
            raise RuntimeError("The requested native voice profile has not been deployed.")
        config.update(voice_agent_name=profile["name"], voice_agent_version=profile["version"])
    if config["project_state"] != "Succeeded" or not config["voice_agent_version"]:
        raise RuntimeError("Confirm the project and native voice version before verification.")
    definition = Foundry(config).request(
        "GET", f"/agents/{config['voice_agent_name']}/versions/{config['voice_agent_version']}?api-version=v1",
        headers={"Foundry-Features": "VoiceAgents=V1Preview"},
    )["definition"]
    marker = uuid.uuid4().hex
    url = config["project_endpoint"].replace("https://", "wss://", 1)
    url += (f"/agents/{config['voice_agent_name']}/endpoint/protocols/voice"
            f"?api-version=v1&agent_session_id={uuid.uuid4()}")
    headers = {
        "Authorization": "Bearer " + Foundry(config).token(),
        "Foundry-Features": "VoiceAgents=V1Preview",
        "x-agent-version-override": str(config["voice_agent_version"]),
    }
    try:
        socket = websocket.create_connection(url, header=headers, timeout=60)
    except websocket.WebSocketException as exc:
        raise RuntimeError(f"Voice connection failed: {type(exc).__name__}.") from exc
    deadline = time.monotonic() + 150
    ready, greeting, sent = False, False, False
    grace, requested_id, status = None, None, None
    transcript, final_text, audio_bytes = [], "", 0
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if not sent and ready and not greeting and grace is not None and now >= grace:
                socket.send(json.dumps({
                    "type": "conversation.item.create",
                    "item": {"type": "message", "role": "user", "content": [{
                        "type": "input_text",
                        "text": f"In the selected {locale} voice profile's language, introduce yourself "
                                "and explain that this is an AI-powered synthetic banking demonstration "
                                "with no real money, and a human can be requested. Use four short sentences. "
                                "Do not discuss customer data or call banking tools.",
                    }]},
                }))
                socket.send(json.dumps({
                    "type": "response.create", "response": {"metadata": {MARKER: marker}},
                }))
                sent = True
            receive_timeout = min(30, deadline - now)
            if not sent and grace is not None and not greeting:
                receive_timeout = min(receive_timeout, grace - now)
            socket.settimeout(max(0.1, receive_timeout))
            try:
                raw = socket.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw:
                raise RuntimeError("Voice connection closed before the requested response completed.")
            event = json.loads(raw)
            kind = event.get("type", "")
            if kind == "error":
                raise RuntimeError("Voice service error: " + json.dumps(event.get("error", event)))
            if not sent:
                if kind == "session.updated":
                    ready = True
                    if not greeting:
                        grace = time.monotonic() + 1
                elif kind == "response.created" or kind.startswith("response.output_"):
                    greeting, grace = True, None
                elif kind == "response.done" and greeting:
                    greeting, grace = False, time.monotonic()
                continue
            if kind == "response.created":
                response = event.get("response", {})
                if response.get("metadata", {}).get(MARKER) == marker:
                    requested_id = response.get("id")
                    if not requested_id:
                        raise RuntimeError("Requested voice response has no ID.")
                continue
            response_id = event.get("response_id") or event.get("response", {}).get("id")
            if not requested_id or response_id != requested_id:
                continue
            if kind == "response.output_audio_transcript.delta":
                transcript.append(event.get("delta", ""))
            elif kind == "response.output_audio_transcript.done":
                final_text = event.get("transcript", "")
            elif kind == "response.output_audio.delta":
                audio_bytes += len(base64.b64decode(event.get("delta", ""), validate=True))
            elif kind == "response.done":
                status = event.get("response", {}).get("status")
                break
    except websocket.WebSocketException as exc:
        raise RuntimeError(f"Voice transport failed: {type(exc).__name__}.") from exc
    finally:
        socket.close()
    text = (final_text or "".join(transcript)).strip()
    result = {"locale": locale, "model": definition["model"], "session_updated": ready,
              "response_id": requested_id, "status": status,
              "transcript": text, "audio_bytes": audio_bytes}
    print(json.dumps(result, indent=2))
    if not ready or status != "completed" or not text or audio_bytes <= 0:
        raise RuntimeError("Native voice did not complete the requested transcript and audio turn.")
    print("Audio round trip confirmed. Microphone, speech recognition and banking tools are not covered.")


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--locale", choices=["en-GB", "az-AZ", "ru-RU"], default="en-GB")
        verify(parser.parse_args().locale)
    except (FoundryError, RuntimeError, ValueError, OSError) as exc:
        print(str(exc))
        raise SystemExit(1)
