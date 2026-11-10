"""Cloud boundary tests use synthetic trusted-broker headers, not real sign-in."""

import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from cloud_app import app
from commerce import DomainError
from foundry import FoundryError
from hosted_banking import HostedBanking

OWNER = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
TENANT = "00000000-0000-0000-0000-000000000003"
ORIGIN = "https://bank.example.com"


def broker(owner=OWNER, tenant=TENANT):
    value = {"auth_typ": "aad", "claims": [{"typ": "tid", "val": tenant}, {"typ": "oid", "val": owner}]}
    return base64.b64encode(json.dumps(value).encode()).decode()


class CloudTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        values = {
            "WEBSITE_SITE_NAME": "test-bank", "CLOUD_EXPECTED_ORIGIN": ORIGIN,
            "CLOUD_AUTH_TENANT": TENANT, "CLOUD_ALLOWED_PRINCIPAL_IDS": OWNER + "," + OTHER,
            "CLOUD_REVIEWER_IDS": OWNER, "CLOUD_CSRF_SECRET": "synthetic-test-secret",
            "BANKING_DATABASE": str(Path(temp.name) / "bank.db"),
        }
        for context in (patch.dict("os.environ", values, clear=True),
                        patch("settings.ENV_FILE", Path(temp.name) / ".env")):
            context.start()
            self.addCleanup(context.stop)
        self.client = TestClient(app, base_url=ORIGIN, headers={"X-MS-CLIENT-PRINCIPAL": broker()})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.token = self.client.get("/api/bank/config").json()["csrf_token"]

    def post(self, operation, body, **kwargs):
        return self.client.post("/api/bank/" + operation, json=body,
                                headers={"Origin": ORIGIN, "X-Baku-Token": self.token}, **kwargs)

    def session(self, customer="leyla", locale=None):
        value = self.post("start", {"customer": customer, **({"locale": locale} if locale else {})})
        self.assertEqual(value.status_code, 201)
        return value.json()["session"]

    def test_missing_and_malformed_principals_are_refused(self):
        for value in ("", "not-base64", base64.b64encode(b"[]").decode(),
                      base64.b64encode(b'{"claims":null}').decode(), broker(tenant=OTHER)):
            self.assertEqual(self.client.get("/banking", headers={"X-MS-CLIENT-PRINCIPAL": value}).status_code, 401)

    def test_host_is_bound_to_the_canonical_application(self):
        self.assertEqual(self.client.get("/banking", headers={"Host": "foreign.example.com"}).status_code, 403)

    def test_unknown_presenter_is_refused(self):
        self.assertEqual(self.client.get("/banking", headers={"X-MS-CLIENT-PRINCIPAL": broker(TENANT)}).status_code, 401)

    def test_security_headers_and_asset_allowlist(self):
        response = self.client.get("/banking")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertEqual(self.client.get("/.env").status_code, 404)
        self.assertEqual(self.client.get("/voice.js").status_code, 200)
        self.assertEqual(self.client.get("/pcm-worklet.js").status_code, 200)

    def test_banking_page_has_no_survey_section(self):
        page = self.client.get("/banking").text
        self.assertNotIn('id="survey"', page)
        self.assertNotIn('class="survey-close"', page)
        self.assertNotIn("Open Agentic Commerce survey", page)
        script = self.client.get("/banking.js").text
        self.assertNotIn('$("survey")', script)

    def test_no_origin_or_csrf_cannot_create_sessions(self):
        self.assertEqual(self.client.post("/api/bank/start", json={"customer": "leyla"}).status_code, 403)
        self.assertEqual(self.client.post("/api/bank/start", json={"customer": "leyla"},
                         headers={"Origin": ORIGIN, "X-Baku-Token": "invalid"}).status_code, 403)

    def test_body_shape_type_and_size_are_bounded(self):
        self.assertEqual(self.post("start", []).status_code, 400)
        self.assertEqual(self.post("start", {"customer": []}).status_code, 400)
        self.assertEqual(self.post("start", {"customer": "leyla", "extra": "x" * 8192}).status_code, 413)
        self.assertEqual(self.client.post("/api/bank/start", content="bad",
                         headers={"Origin": ORIGIN, "X-Baku-Token": self.token,
                                  "Content-Type": "text/plain"}).status_code, 415)

    def test_session_owner_cannot_be_replaced_by_another_presenter(self):
        session = self.session()
        self.client.headers["X-MS-CLIENT-PRINCIPAL"] = broker(OTHER)
        self.token = self.client.get("/api/bank/config").json()["csrf_token"]
        self.assertEqual(self.post("state", {"session": session}).status_code, 403)
        self.assertEqual(self.post("action", {"session": session, "operation": "profile"}).status_code, 403)

    def test_hosted_failure_is_explicit_and_never_uses_local_success(self):
        session = self.session()
        with patch.object(app.state.hosted, "action", side_effect=FoundryError("Hosted execution failed.")), \
                patch.object(app.state.bank, "action") as local:
            self.assertEqual(self.post("action", {"session": session, "operation": "profile"}).status_code, 502)
            local.assert_not_called()

    def test_authorized_human_uses_real_broker_attribution(self):
        session = self.session()
        with patch.object(app.state.bank, "decide", return_value={"status": "indicative"}) as decide:
            self.assertEqual(self.post("decision", {"session": session, "decision": "approved",
                                                    "acknowledged": True}).status_code, 200)
            self.assertEqual(decide.call_args.kwargs["actor"], "human:" + OWNER)

    def test_nonreviewer_cannot_decide_even_with_own_session(self):
        self.client.headers["X-MS-CLIENT-PRINCIPAL"] = broker(OTHER)
        config = self.client.get("/api/bank/config").json()
        self.assertFalse(config["reviewer"])
        self.token = config["csrf_token"]
        session = self.session()
        with patch.object(app.state.bank, "decide") as decide:
            self.assertEqual(self.post("decision", {"session": session, "decision": "approved",
                                                    "acknowledged": True}).status_code, 403)
            decide.assert_not_called()

    def test_websocket_refuses_wrong_origin_and_foreign_session(self):
        from starlette.websockets import WebSocketDisconnect
        session = self.session()
        with self.assertRaises(WebSocketDisconnect) as caught:
            with self.client.websocket_connect("/api/bank/voice?session=" + session,
                                               headers={"Origin": "https://foreign.example.com"}):
                pass
        self.assertEqual(caught.exception.code, 1008)
        self.assertFalse(app.state.active_calls)

    def test_websocket_bad_csrf_releases_active_call(self):
        session = self.session()
        with self.client.websocket_connect("/api/bank/voice?session=" + session,
                                           headers={"Origin": ORIGIN, "Host": "bank.example.com"}) as socket:
            socket.send_json({"type": "start", "csrf_token": "invalid"})
            self.assertEqual(socket.receive_json()["type"], "error")
        self.assertFalse(app.state.active_calls)

    def test_selected_start_locale_reaches_server_state_and_invalid_locale_is_refused(self):
        for locale in ("en-GB", "ru-RU", "az-AZ"):
            session = self.session(locale=locale)
            self.assertEqual(self.post("state", {"session": session}).json()["locale"], locale)
        self.assertEqual(self.post("start", {"customer": "leyla", "locale": "fr-FR"}).status_code, 400)

    def test_public_voice_profiles_expose_only_locale_and_version(self):
        profiles = self.client.get("/api/bank/config").json()["voice_profiles"]
        self.assertEqual(set(profiles), {"en-GB", "ru-RU", "az-AZ"})
        self.assertTrue(all(set(profile) == {"version"} for profile in profiles.values()))

    def test_each_scene_and_language_uses_exact_native_profile_and_version(self):
        class Native:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def __aiter__(self):
                yield json.dumps({"type": "session.updated"})

        config = {"project_endpoint": "https://example.services.ai.azure.com/api/projects/test",
                  "voice_agent_name": "legacy", "voice_agent_version": "1",
                  "voice_profiles": {
                      locale: {"name": name, "version": version}
                      for locale, name, version in
                      (("en-GB", "test-en", "6"), ("ru-RU", "test-ru", "2"), ("az-AZ", "test-az", "7"))}}
        for customer in ("leyla", "dmitri", "james"):
            for locale, profile in config["voice_profiles"].items():
                with self.subTest(customer=customer, locale=locale):
                    session = self.session(customer, locale)
                    with patch.object(app.state.client, "config", config), \
                            patch.object(app.state.client, "token", return_value="synthetic-test-token"), \
                            patch("voice_bridge.connect", return_value=Native()) as connect:
                        with self.client.websocket_connect("/api/bank/voice?session=" + session,
                                headers={"Origin": ORIGIN, "Host": "bank.example.com"}) as socket:
                            socket.send_json({"type": "start", "csrf_token": self.token})
                            self.assertEqual(socket.receive_json(), {"type": "ready", "locale": locale})
                            self.assertEqual(socket.receive()["type"], "websocket.close")
                        self.assertIn(f"/agents/{profile['name']}/endpoint/protocols/voice?", connect.call_args.args[0])
                        self.assertEqual(connect.call_args.kwargs["additional_headers"]["x-agent-version-override"],
                                         profile["version"])
                    self.assertFalse(app.state.active_calls)

    def test_missing_local_voice_never_falls_back_to_english(self):
        config = {"voice_agent_name": "legacy-english", "voice_agent_version": "1"}
        for locale in ("ru-RU", "az-AZ"):
            with self.subTest(locale=locale):
                session = self.session(locale=locale)
                with patch.object(app.state.client, "config", config), patch("voice_bridge.connect") as connect:
                    with self.client.websocket_connect("/api/bank/voice?session=" + session,
                            headers={"Origin": ORIGIN, "Host": "bank.example.com"}) as socket:
                        socket.send_json({"type": "start", "csrf_token": self.token})
                        event = socket.receive_json()
                        self.assertEqual(event["type"], "error")
                        self.assertIn("No English voice has been substituted", event["error"]["message"])
                    connect.assert_not_called()
                self.assertFalse(app.state.active_calls)

    def test_typed_voice_waits_for_greeting_and_prevents_overlapping_turns(self):
        class Native:
            async def __aenter__(self):
                self.events, self.sent = asyncio.Queue(), []
                await self.events.put(json.dumps({"type": "session.updated"}))
                return self

            async def __aexit__(self, *args):
                pass

            def __aiter__(self):
                return self

            async def __anext__(self):
                event = await self.events.get()
                if event is None:
                    raise StopAsyncIteration
                return event

            async def send(self, raw):
                value = json.loads(raw)
                self.sent.append(value)
                if value["type"] == "response.create":
                    await self.events.put(json.dumps({"type": "response.created", "response": {"id": "turn"}}))

        native, session = Native(), self.session(locale="en-GB")
        config = {"project_endpoint": "https://example.services.ai.azure.com/api/projects/test",
                  "voice_agent_name": "test-voice", "voice_agent_version": "1"}
        with patch.object(app.state.client, "config", config), \
                patch.object(app.state.client, "token", return_value="synthetic-test-token"), \
                patch("voice_bridge.connect", return_value=native), \
                patch.object(app.state.hosted, "action", return_value={
                    "name": "Synthetic caller", "hosted_execution": {"role": "orchestrator"}
                }) as hosted:
            with self.client.websocket_connect("/api/bank/voice?session=" + session,
                    headers={"Origin": ORIGIN, "Host": "bank.example.com"}) as socket:
                socket.send_json({"type": "start", "csrf_token": self.token})
                self.assertEqual(socket.receive_json()["type"], "ready")
                socket.send_json({"type": "text", "text": "Too early"})
                self.assertEqual(socket.receive_json()["type"], "input_refused")
                self.assertEqual(native.sent, [])
                for event in ({"type": "response.created", "response": {"id": "greeting"}},
                              {"type": "response.done", "response": {"id": "greeting", "status": "completed"}}):
                    self.client.portal.call(native.events.put, json.dumps(event))
                self.assertTrue(socket.receive_json()["busy"])
                self.assertEqual(socket.receive_json()["type"], "response.done")
                self.assertFalse(socket.receive_json()["busy"])
                socket.send_json({"type": "text", "text": "Show my profile"})
                self.assertTrue(socket.receive_json()["busy"])
                socket.send_json({"type": "text", "text": "Overlapping"})
                self.assertEqual(socket.receive_json()["type"], "input_refused")
                self.assertEqual(sum(event["type"] == "response.create" for event in native.sent), 1)
                self.client.portal.call(native.events.put, json.dumps({
                    "type": "response.function_call_arguments.done", "call_id": "profile-call",
                    "name": "banking_action",
                    "arguments": json.dumps({"operation": "profile", "payload": "{}"}),
                }))
                tool = socket.receive_json()
                self.assertEqual(tool["type"], "tool_result")
                self.assertEqual(tool["operation"], "profile")
                self.assertEqual(tool["result"]["hosted_execution"]["role"], "orchestrator")
                hosted.assert_called_once_with(session, "profile", {})
                self.client.portal.call(native.events.put, None)
                for _ in range(3):
                    if socket.receive()["type"] == "websocket.close":
                        break
                else:
                    self.fail("Native connection did not close.")
        self.assertFalse(app.state.active_calls)


class HostedBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.bank, self.client = Mock(), Mock()
        self.bank.view.return_value = {"customer": "leyla", "locale": "az-AZ", "verified": True,
                                       "card": "active", "messages": [{"text": "synthetic-secret-otp"}]}
        self.bank.action.return_value = {"frozen": True}
        self.hosted = HostedBanking(self.bank, self.client)

    def response(self, method, path, body):
        return {"role": "card", "agent": "banking-card", "operation": body["operation"],
                "request_id": body["request_id"], "model_response_id": "response-test",
                "orchestrator": "banking-orchestrator"}

    def test_worker_confirmation_is_required_before_bank_mutation(self):
        self.client.request.return_value = {"role": "card"}
        with self.assertRaises(FoundryError):
            self.hosted.action("session", "freeze_card")
        self.bank.action.assert_not_called()

    def test_credentials_and_otp_do_not_enter_worker_context(self):
        self.client.request.side_effect = self.response
        result = self.hosted.action("session", "freeze_card", {"otp": "synthetic-private-input"})
        request = self.client.request.call_args.args[2]
        self.assertNotIn("otp", json.dumps(request))
        self.assertNotIn("messages", request["context"])
        self.assertEqual(result["hosted_execution"]["agent"], "banking-card")
        self.assertEqual(self.bank.action.call_args.kwargs["actor"], "hosted:banking-card")
        self.assertIn("evidence", self.bank.action.call_args.kwargs)

    def test_incomplete_or_forged_execution_references_are_refused(self):
        for field, value in (("agent", "different"), ("model_response_id", None),
                             ("orchestrator", "different"), ("request_id", "different")):
            def response(method, path, body):
                result = self.response(method, path, body)
                result[field] = value
                return result
            self.client.request.side_effect = response
            with self.assertRaises(FoundryError):
                self.hosted.action("session", "freeze_card")
        self.bank.action.assert_not_called()



GATEWAY = "00000000-0000-0000-0000-000000000009"


class GatewayRouteTests(unittest.TestCase):
    """The Toolbox connection identity is accepted on the gateway route only."""

    def setUp(self):
        values = patch("cloud_app.read_environment", side_effect=self.environment)
        values.start()
        self.addCleanup(values.stop)
        CloudTests.setUp(self)

    def environment(self, *args, **kwargs):
        from settings import read_environment
        env = read_environment(*args, **kwargs)
        env["CLOUD_GATEWAY_PRINCIPAL_IDS"] = GATEWAY
        return env

    def rpc(self, body, principal=GATEWAY, **headers):
        return self.client.post("/gateway/mcp", content=json.dumps(body),
                                headers={"Content-Type": "application/json",
                                         "X-MS-CLIENT-PRINCIPAL": broker(principal), **headers})

    def test_gateway_identity_lists_the_single_governed_tool(self):
        response = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, Host="bank.example.com:443")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([tool["name"] for tool in response.json()["result"]["tools"]],
                         ["execute_banking_operation"])
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code, 202)

    def test_identities_cannot_cross_routes(self):
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, OWNER).status_code, 401)
        self.assertEqual(self.client.get("/api/bank/config",
                                         headers={"X-MS-CLIENT-PRINCIPAL": broker(GATEWAY)}).status_code, 401)
        self.assertEqual(self.client.get("/banking", headers={"X-MS-CLIENT-PRINCIPAL": broker(GATEWAY)}).status_code, 401)

    def test_gateway_rejects_wrong_media_and_oversized_bodies(self):
        response = self.client.post("/gateway/mcp", content="{}", headers={
            "Content-Type": "text/plain", "X-MS-CLIENT-PRINCIPAL": broker(GATEWAY)})
        self.assertEqual(response.status_code, 415)
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping", "pad": "x" * 9000}).status_code, 413)

    def test_forged_grant_executes_nothing(self):
        response = self.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "execute_banking_operation", "arguments": {
                "grant": "forged", "agent": "banking-card", "role": "card",
                "request_id": "r", "model_response_id": "m"}}})
        self.assertTrue(response.json()["result"]["isError"])


class GovernedBrokerTests(unittest.TestCase):
    """Broker -> fake hosted orchestrator -> real gateway handler -> real deterministic bank."""

    def setUp(self):
        from banking import Bank
        from gateway import Grants
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.bank = Bank(Path(temp.name) / "bank.db")
        with self.bank.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS bank_owners(session TEXT PRIMARY KEY,owner TEXT NOT NULL)")
        self.sessions = {}
        for owner, customer in ((OWNER, "leyla"), (OTHER, "leyla"), (OWNER, "james")):
            session = self.bank.start(customer)["session"]
            with self.bank.db() as db:
                db.execute("INSERT INTO bank_owners VALUES(?,?)", (session, owner))
            self.sessions[(owner, customer)] = session
        self.grants, self.calls, self.created = Grants(), [], 0
        self.client = Mock()
        self.client.request.side_effect = self.foundry
        self.hosted = HostedBanking(self.bank, self.client, self.grants, governed=True,
                                    isolation_secret="synthetic-isolation-secret", version="2")
        self.present = True

    def foundry(self, method, path, body, headers=None):
        import gateway
        self.calls.append((method, path, body, dict(headers or {})))
        if path.endswith("/sessions?api-version=v1"):
            self.created += 1
            return {"agent_session_id": f"sess{self.created}"}
        from hosted_banking import ROUTES
        role = ROUTES.get(body["operation"], "orchestrator")
        result = {"role": role, "agent": "banking-" + role, "operation": body["operation"],
                  "request_id": body["request_id"], "model_response_id": "resp-test",
                  "orchestrator": "banking-orchestrator"}
        if self.present:
            arguments = {key: result[key] for key in ("agent", "role", "request_id", "model_response_id")}
            status, reply = gateway.handle(self.bank, self.grants, {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": gateway.TOOL, "arguments": {"grant": body["grant"], **arguments}}})
            receipt = reply["result"].get("structuredContent")
            if reply["result"].get("isError"):
                raise FoundryError("409 refused")
            result["gateway"] = receipt
        return result

    def test_governed_action_runs_only_through_the_gateway(self):
        session = self.sessions[(OWNER, "leyla")]
        result = self.hosted.action(session, "freeze_card")
        self.assertIn("error", result)  # unverified: the deterministic bank refuses inside the gateway
        self.assertEqual(self.bank.view(session)["card"], "active")
        invoke = [call for call in self.calls if "invocations" in call[1]][0]
        self.assertIn("grant", invoke[2])
        self.assertIn("agent_session_id=sess1", invoke[1])
        self.assertEqual(result["hosted_execution"]["gateway_receipt"]["executed"], True)
        self.assertEqual(result["hosted_execution"]["gateway"],
                         "Foundry Toolbox MCP -> governed banking gateway")

    def test_missing_gateway_call_reports_no_success_and_changes_nothing(self):
        self.present = False
        session = self.sessions[(OWNER, "leyla")]
        before = self.bank.view(session)
        with self.assertRaises(FoundryError):
            self.hosted.action(session, "freeze_card")
        self.assertEqual(self.bank.view(session)["card"], before["card"])

    def test_grants_are_single_use_and_bound(self):
        import gateway
        session = self.sessions[(OWNER, "leyla")]
        grant = self.grants.issue(session, "profile", None, "orchestrator", "req")
        arguments = {"grant": grant, "agent": "banking-card", "role": "card", "request_id": "req",
                     "model_response_id": "m"}
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": gateway.TOOL, "arguments": arguments}}
        self.assertTrue(gateway.handle(self.bank, self.grants, call)[1]["result"]["isError"])
        arguments.update(agent="banking-orchestrator", role="orchestrator")
        self.assertFalse(gateway.handle(self.bank, self.grants, call)[1]["result"]["isError"])
        self.assertTrue(gateway.handle(self.bank, self.grants, call)[1]["result"]["isError"])
        clock = [0]
        expiring = gateway.Grants(ttl=1, clock=lambda: clock[0])
        token = expiring.issue(session, "profile", None, "orchestrator", "r2")
        clock[0] = 5
        with self.assertRaises(DomainError):
            expiring.claim(token, {"request_id": "r2", "role": "orchestrator",
                                   "agent": "banking-orchestrator", "model_response_id": "m"})

    def test_otp_and_payload_stay_server_side(self):
        session = self.sessions[(OWNER, "leyla")]
        self.hosted.action(session, "verify_complete", {"otp": "135790"})
        for _, _, body, headers in self.calls:
            self.assertNotIn("135790", json.dumps(body) + json.dumps(headers))

    def test_isolation_identity_is_server_derived_per_owner_and_customer(self):
        identities = set()
        for key, session in self.sessions.items():
            self.hosted.action(session, "profile")
            identities.add(self.calls[-1][3]["x-ms-user-identity"])
        self.assertEqual(len(identities), 3)
        for identity in identities:
            self.assertRegex(identity, r"^cust-[0-9a-f]{48}$")
            self.assertNotIn("leyla", identity)
        self.hosted.action(self.sessions[(OWNER, "leyla")], "profile")
        self.assertEqual(self.created, 3)
        session_body = [call for call in self.calls if call[1].endswith("/sessions?api-version=v1")][0][2]
        self.assertEqual(session_body["version_indicator"]["agent_version"], "2")

    def test_expired_hosted_session_is_replaced_once(self):
        session = self.sessions[(OWNER, "james")]
        self.hosted.action(session, "profile")
        original = self.client.request.side_effect
        failed = []

        def expire(method, path, body, headers=None):
            if "invocations" in path and not failed:
                failed.append(path)
                error = FoundryError("gone")
                error.status = 404
                raise error
            return original(method, path, body, headers)
        self.client.request.side_effect = expire
        self.hosted.action(session, "profile")
        self.assertEqual(self.created, 2)

    def test_knowledge_query_is_audited_without_state_change(self):
        session = self.sessions[(OWNER, "leyla")]
        result = self.hosted.action(session, "knowledge", {"query": "What is the card freeze policy?"})
        self.assertEqual(result["read_only"], True)
        sent = [call for call in self.calls if "invocations" in call[1]][-1][2]
        self.assertEqual(sent["context"]["query"], "What is the card freeze policy?")
        self.hosted.action(session, "knowledge", {"query": "My card 4111 is blocked"})
        sent = [call for call in self.calls if "invocations" in call[1]][-1][2]
        self.assertNotIn("query", sent["context"])


if __name__ == "__main__":
    unittest.main()
