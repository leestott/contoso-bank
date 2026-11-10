"""Server-owned native voice connection; no Azure credentials reach the browser."""

import asyncio
import base64
import json
import logging
import sqlite3
import uuid

from fastapi import WebSocketDisconnect
from starlette.websockets import WebSocketState
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from commerce import DomainError
from foundry import FoundryError
from hosted_banking import COMMON, ROUTES
import transcripts

LOG = logging.getLogger("banking.voice")
TRANSLATIONS = set()


def record(state, session, speaker, text):
    """Store the original line, then translate non-English lines for staff without blocking audio."""
    entry = state.bank.transcript(session, speaker, text)
    if entry["translation"] != "pending":
        return

    async def translate():
        try:
            value, source = await asyncio.to_thread(transcripts.english, state.client, entry["locale"], text)
        except (FoundryError, ValueError, OSError) as exc:
            LOG.warning("Transcript translation failed: %s", type(exc).__name__)
            value, source = None, "unavailable"
        try:
            await asyncio.to_thread(state.bank.translate, session, entry["id"], value, source)
        except (DomainError, OSError, sqlite3.Error) as exc:
            LOG.warning("Transcript translation not stored: %s", type(exc).__name__)

    task = asyncio.get_running_loop().create_task(translate())
    TRANSLATIONS.add(task)
    task.add_done_callback(TRANSLATIONS.discard)


async def bridge(socket, state):
    from cloud_app import csrf, own_session, principal
    session = socket.query_params.get("session")
    owner = None
    try:
        owner = principal(socket.headers, state.env)
        if socket.headers.get("origin") != state.env["CLOUD_EXPECTED_ORIGIN"]:
            raise DomainError("Same-origin microphone connection required.", 403)
        own_session(state.bank, session, owner)
        if owner in state.active_calls:
            raise DomainError("End the current voice call before opening another.", 409)
    except DomainError:
        await socket.close(code=1008)
        return
    state.active_calls.add(owner)
    try:
        await socket.accept()
        start = await asyncio.wait_for(socket.receive_json(), timeout=10)
        import hmac
        if (not isinstance(start, dict) or start.get("type") != "start"
                or not isinstance(start.get("csrf_token"), str)
                or not hmac.compare_digest(start["csrf_token"].encode(), csrf(state.env, owner).encode())):
            raise DomainError("Refresh the banking page before starting voice.", 403)
        c = state.client.config
        locale = state.bank.view(session)["locale"]
        profile = c.get("voice_profiles", {}).get(locale)
        if profile is None and locale == "en-GB":
            profile = {"name": c["voice_agent_name"], "version": c["voice_agent_version"]}
        if not profile or not profile["version"]:
            raise DomainError(f"Native voice for {locale} is currently unavailable. "
                              "No English voice has been substituted.", 503)
        endpoint = c["project_endpoint"].replace("https://", "wss://", 1)
        endpoint += (f"/agents/{profile['name']}/endpoint/protocols/voice"
                     f"?api-version=v1&agent_session_id={uuid.uuid4()}")
        token = await asyncio.to_thread(state.client.token)
        headers = {"Authorization": "Bearer " + token, "Foundry-Features": "VoiceAgents=V1Preview",
                   "x-agent-version-override": str(profile["version"])}
        async with asyncio.timeout(600), connect(endpoint, additional_headers=headers,
                                                  max_size=2**22, open_timeout=60) as upstream:
            sent_calls = set()
            response_pending, active_response = True, None

            async def caller():
                nonlocal response_pending
                while True:
                    value = await socket.receive_json()
                    if not isinstance(value, dict):
                        raise DomainError("Invalid voice message.")
                    if value.get("type") == "audio":
                        data = value.get("data")
                        if not isinstance(data, str) or len(data) > 32000:
                            raise DomainError("Audio chunks must contain bounded PCM data.")
                        decoded = base64.b64decode(data, validate=True)
                        if len(decoded) % 2:
                            raise DomainError("PCM audio must contain complete 16-bit samples.")
                        await upstream.send(json.dumps({"type": "input_audio_buffer.append", "audio": data}))
                    elif value.get("type") == "text":
                        text = value.get("text")
                        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
                            raise DomainError("Live-agent text must be from 1 to 2000 characters.")
                        if response_pending or active_response:
                            await socket.send_json({"type": "input_refused",
                                "message": "Wait for the current agent turn before sending another typed message."})
                            continue
                        response_pending = True
                        record(state, session, "caller", text)
                        await upstream.send(json.dumps({"type": "conversation.item.create", "item": {
                            "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}))
                        await upstream.send(json.dumps({"type": "response.create"}))
                    else:
                        raise DomainError("Unsupported voice client message.")

            async def agent():
                nonlocal response_pending, active_response
                async for raw in upstream:
                    value = json.loads(raw)
                    kind = value.get("type", "")
                    if kind == "session.updated":
                        await socket.send_json({"type": "ready", "locale": state.bank.view(session)["locale"]})
                    elif kind == "response.created":
                        active_response = value["response"]["id"]
                        response_pending = False
                        await socket.send_json({"type": "turn_state", "busy": True})
                    elif kind == "response.function_call_arguments.done":
                        call_id = value.get("call_id")
                        if not call_id or call_id in sent_calls:
                            continue
                        sent_calls.add(call_id)
                        if len(sent_calls) > 100:
                            raise DomainError("Call tool limit reached. Start another call.")
                        operation = None
                        try:
                            args = json.loads(value.get("arguments", ""))
                            if value.get("name") != "banking_action" or not isinstance(args, dict):
                                raise DomainError("Unknown native voice tool.")
                            operation, payload = args.get("operation"), args.get("payload")
                            if not isinstance(operation, str) or operation not in COMMON | ROUTES.keys():
                                raise DomainError("Unsupported native banking operation.")
                            if not isinstance(payload, str) or len(payload) > 4000:
                                raise DomainError("Tool payload must be a bounded JSON object string.")
                            parsed = json.loads(payload)
                            if not isinstance(parsed, dict):
                                raise DomainError("Tool payload must decode to a JSON object.")
                            result = await asyncio.to_thread(state.hosted.action, session, operation, parsed)
                        except (DomainError, FoundryError, ValueError) as exc:
                            LOG.warning("Native banking tool failed: %s", type(exc).__name__)
                            result = {"error": str(exc), "action_executed": False}
                        await upstream.send(json.dumps({"type": "conversation.item.create", "item": {
                            "type": "function_call_output", "call_id": call_id, "output": json.dumps(result)}}))
                        await socket.send_json({"type": "tool_result", "operation": operation, "result": result})
                        response_pending = True
                        await upstream.send(json.dumps({"type": "response.create"}))
                    elif kind == "conversation.item.input_audio_transcription.completed":
                        text = value.get("transcript", "")
                        if text:
                            record(state, session, "caller", text[:4000])
                        await socket.send_json(value)
                    elif kind == "response.output_audio_transcript.done":
                        text = value.get("transcript", "")
                        if text:
                            record(state, session, "agent", text[:4000])
                        await socket.send_json(value)
                    elif kind in {"response.output_audio.delta", "response.output_audio_transcript.delta",
                                  "input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped",
                                  "response.done", "error"}:
                        if kind == "response.done" and value.get("response", {}).get("status") in {"failed", "incomplete"}:
                            raise FoundryError("The native voice turn failed or was truncated. No spoken success is confirmed.")
                        await socket.send_json(value)
                        if kind == "response.done":
                            if value.get("response", {}).get("id") == active_response:
                                active_response = None
                            await socket.send_json({"type": "turn_state",
                                                    "busy": bool(response_pending or active_response)})

            tasks = [asyncio.create_task(caller()), asyncio.create_task(agent())]
            try:
                finished, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in finished:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    except (WebSocketException, FoundryError, DomainError, TimeoutError, ValueError, OSError) as exc:
        LOG.warning("Native voice connection ended: %s", type(exc).__name__)
        try:
            await socket.send_json({"type": "error", "error": {"message": str(exc)}})
            await socket.close(code=1011)
        except (WebSocketDisconnect, RuntimeError):
            LOG.info("Browser disconnected before voice error delivery.")
    finally:
        state.active_calls.discard(owner)
        if (socket.client_state == WebSocketState.CONNECTED
                and socket.application_state == WebSocketState.CONNECTED):
            await socket.close(code=1000)
