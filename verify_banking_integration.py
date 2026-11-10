"""Live Foundry + local ASGI acceptance, NOT a live Entra or hardware-microphone test."""

import argparse
import base64
import json
import os
import tempfile
import time
import wave
from pathlib import Path
from unittest.mock import patch


def verify(wav_path=None):
    from fastapi.testclient import TestClient
    from cloud_app import app
    from banking import CUSTOMERS
    owner = "00000000-0000-0000-0000-000000000001"
    tenant = "00000000-0000-0000-0000-000000000002"
    origin = "https://bank.example.com"
    principal = base64.b64encode(json.dumps({"auth_typ": "aad", "claims": [
        {"typ": "oid", "val": owner}, {"typ": "tid", "val": tenant}]}).encode()).decode()
    with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {
        "WEBSITE_SITE_NAME": "local-asgi-acceptance", "CLOUD_EXPECTED_ORIGIN": origin,
        "CLOUD_AUTH_TENANT": tenant, "CLOUD_ALLOWED_PRINCIPAL_IDS": owner,
        "CLOUD_REVIEWER_IDS": owner, "CLOUD_CSRF_SECRET": "local-acceptance-only",
        "BANKING_DATABASE": str(Path(temporary) / "bank.db"),
    }), TestClient(app, base_url=origin, headers={"X-MS-CLIENT-PRINCIPAL": principal}) as client:
        token = client.get("/api/bank/config").json()["csrf_token"]
        def post(operation, body, expected_refusal=False):
            response = client.post("/api/bank/" + operation, json=body,
                                   headers={"Origin": origin, "X-Baku-Token": token})
            value = response.json()
            refused = expected_refusal and value.get("flagged") is True
            if not response.is_success or (value.get("error") and not refused):
                raise RuntimeError("Live integration request failed: " + json.dumps(value))
            return value

        def action(session, operation, payload=None, expected_refusal=False):
            value = post("action", {"session": session, "operation": operation, "payload": payload or {}},
                         expected_refusal=expected_refusal)
            if not value.get("hosted_execution", {}).get("model_response_id"):
                raise RuntimeError("Live governed action has no confirmed hosted evidence.")
            return value

        def start(customer):
            session = post("start", {"customer": customer})["session"]
            action(session, "verify_start", {"date_of_birth": CUSTOMERS[customer]["dob"]})
            otp = post("state", {"session": session})["messages"][-1]["text"]
            if not action(session, "verify_complete", {"otp": otp}).get("verified"):
                raise RuntimeError("Synthetic OTP verification was not confirmed.")
            return session

        leyla = start("leyla")
        action(leyla, "freeze_card")
        assert len(action(leyla, "transactions")["transactions"]) == 3
        assert action(leyla, "replace_card")["replacement_to"] == CUSTOMERS["leyla"]["address"]
        assert action(leyla, "virtual_card")["virtual_card"] == "ready"
        assert action(leyla, "profile", {"customer_id": "james"}, expected_refusal=True)["flagged"]
        print("Live hosted/governed lost-card journey and cross-customer refusal confirmed.", flush=True)

        dmitri = start("dmitri")
        action(dmitri, "set_locale", {"locale": "en-GB"})
        assert len(action(dmitri, "statements")["statements"]) == 12
        assert len(action(dmitri, "prepare_memo", {"amount": 50000})["memo"]["pages"]) == 2
        assert post("decision", {"session": dmitri, "decision": "indicative_offer",
                                "acknowledged": True})["status"] == "indicative_offer"
        print("Live hosted/governed lending journey and authenticated-broker human decision confirmed.", flush=True)

        james = start("james")
        action(james, "offers")
        mandate = action(james, "create_mandate", {"confirmed": True, "passcode": "246810"})["mandate"]
        for offer in ("renewal-existing", "electricity"):
            assert action(james, "pay", {"offer_id": offer, "mandate_id": mandate["id"]}).get("payment")
        blocked = action(james, "pay", {"offer_id": "flight-upgrade", "mandate_id": mandate["id"]})
        assert blocked["blocked"] and {"unapproved_merchant", "per_transaction_limit"} <= set(blocked["reasons"])
        assert action(james, "one_off_request", {"offer_id": "flight-upgrade",
                      "mandate_id": mandate["id"]})["request"]["does_not_override_mandate"]
        audit = post("audit", {"session": james})
        assert audit["chain_valid"]
        assert any(event["actor"] == "human:" + owner for event in audit["events"])
        assert any(event["detail"].get("hosted_execution") for event in audit["events"])
        print("Live hosted/governed payments, double block, one-off request and audit confirmed.", flush=True)

        if wav_path:
            with wave.open(str(wav_path), "rb") as audio:
                if (audio.getframerate(), audio.getsampwidth(), audio.getnchannels()) != (24000, 2, 1):
                    raise ValueError("Caller recording must be mono PCM16 at 24000 Hz.")
                pcm = b"\0" * 48000 + audio.readframes(audio.getnframes()) + b"\0" * 96000
            with client.websocket_connect("/api/bank/voice?session=" + james,
                    headers={"Origin": origin, "Host": "bank.example.com"}) as socket:
                socket.send_json({"type": "start", "csrf_token": token})
                for _ in range(1500):
                    event = socket.receive_json()
                    if event["type"] == "error":
                        raise RuntimeError(json.dumps(event))
                    if event["type"] == "response.done":
                        break
                else:
                    raise RuntimeError("Native greeting did not complete.")
                for offset in range(0, len(pcm), 4800):
                    socket.send_json({"type": "audio", "data": base64.b64encode(pcm[offset:offset+4800]).decode()})
                    time.sleep(0.1)
                transcript, tool, spoken, audio_bytes = "", None, "", 0
                for _ in range(2000):
                    event = socket.receive_json()
                    kind = event["type"]
                    if kind == "error":
                        raise RuntimeError(json.dumps(event))
                    if kind == "conversation.item.input_audio_transcription.completed":
                        transcript = event["transcript"]
                    elif kind == "tool_result":
                        tool = event["result"]
                    elif kind == "response.output_audio.delta":
                        audio_bytes += len(base64.b64decode(event["delta"], validate=True))
                    elif kind == "response.output_audio_transcript.done":
                        spoken += event["transcript"]
                    elif kind == "response.done" and tool and spoken and audio_bytes:
                        break
                if not transcript or not tool or not tool.get("hosted_execution") or not spoken or not audio_bytes:
                    raise RuntimeError("Recorded PCM/STT/native function/hosted/audio chain was not confirmed.")
                print(json.dumps({"recorded_caller_transcript": transcript, "native_tool": tool["hosted_execution"],
                                  "spoken_transcript": spoken, "audio_bytes": audio_bytes}), flush=True)
            print("Recorded PCM -> native STT -> banking tool -> hosted worker -> speech confirmed.", flush=True)
        print("This acceptance does NOT verify deployed Entra sign-in or browser microphone hardware.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True, help="Perform billed real Foundry invocations.")
    parser.add_argument("--caller-wav", type=Path)
    args = parser.parse_args()
    verify(args.caller_wav)
