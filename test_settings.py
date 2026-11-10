"""Configuration and publication checks use temporary, synthetic settings only."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

import settings
from check_publication import findings, private_path, private_values
from foundry import Foundry, FoundryError


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / ".env"
        environment = patch.dict("os.environ", {}, clear=True)
        source = patch("settings.ENV_FILE", self.path)
        environment.start()
        source.start()
        self.addCleanup(environment.stop)
        self.addCleanup(source.stop)

    def test_missing_file_supports_offline_without_cloud_credentials(self):
        self.assertEqual(settings.read_environment(), {})
        self.assertEqual(settings.load_config()["project_endpoint"], "")
        self.assertEqual(settings.load_config("banking")["project_state"], "Unknown")
        client = Foundry()
        with self.assertRaisesRegex(FoundryError, "endpoint"):
            client.request("GET", "/agents?api-version=v1")
        with self.assertRaisesRegex(FoundryError, "COMMERCE_AGENT_NAME"):
            client.recommend("Synthetic", [], 1, {})
        with self.assertRaisesRegex(FoundryError, "AZURE_SUBSCRIPTION_ID"):
            client.token()

    def test_literal_values_quotes_comments_and_process_precedence(self):
        self.path.write_text(
            '# Local only\nexport FIRST = "hello # world" # comment\n'
            "SECOND='quoted value' # note\nTHIRD=plain # comment\n"
            "LITERAL=$(never-execute)\nEMPTY=\n", encoding="utf-8")
        values = settings.read_environment(environ={"FIRST": "process value"})
        self.assertEqual(values, {"FIRST": "process value", "SECOND": "quoted value",
                                 "THIRD": "plain", "LITERAL": "$(never-execute)", "EMPTY": ""})

    def test_invalid_and_duplicate_assignments_do_not_echo_values(self):
        for body in ("KEY=private-value\nKEY=private-value", "invalid private-value",
                     "KEY='private-value", 'KEY="private-value" trailing', "BAD-KEY=private-value"):
            with self.subTest(body=body):
                self.path.write_text(body, encoding="utf-8")
                with self.assertRaises(ValueError) as caught:
                    settings.read_environment()
                self.assertIn("line", str(caught.exception))
                self.assertNotIn("private-value", str(caught.exception))

    def test_live_settings_are_reloaded_and_exported_values_win(self):
        self.path.write_text("BANKING_VOICE_AGENT_VERSION=2\n", encoding="utf-8")
        self.assertEqual(settings.load_config("banking")["voice_agent_version"], "2")
        self.path.write_text("BANKING_VOICE_AGENT_VERSION=3\n", encoding="utf-8")
        self.assertEqual(settings.load_config("banking")["voice_agent_version"], "3")
        with patch.dict("os.environ", {"BANKING_VOICE_AGENT_VERSION": "4"}):
            self.assertEqual(settings.load_config("banking")["voice_agent_version"], "4")

    def test_native_voice_profiles_keep_independent_version_pins(self):
        self.path.write_text("BANKING_VOICE_AGENT_NAME=test-voice\nBANKING_VOICE_AGENT_VERSION=5\n"
                             "BANKING_VOICE_AZ_AGENT_VERSION=2\nBANKING_VOICE_RU_AGENT_VERSION=1\n",
                             encoding="utf-8")
        profiles = settings.load_config("banking")["voice_profiles"]
        self.assertEqual(profiles["az-AZ"], {"name": "test-voice-az", "version": "2"})
        self.assertEqual(profiles["ru-RU"], {"name": "test-voice-ru", "version": "1"})
        self.assertEqual(profiles["en-GB"], {"name": "test-voice", "version": "5"})

    def test_web_deployment_approves_only_its_origin_and_refuses_readback_drift(self):
        import deploy_web
        config = {"project_state": "Succeeded", "voice_agent_version": "1",
                  "subscription_id": "test-subscription", "tenant_id": "test-tenant",
                  "project_name": "test-project", "resource_group": "test-group",
                  "region": "test-region", "account_name": "test-account"}
        env = {"BANKING_HOSTED_" + role + "_VERSION": "1"
               for role in ("ORCHESTRATOR", "CARD", "LENDING", "PAYMENTS")}
        env["BANKING_WEB_CLIENT_ID"] = "test-client"
        origin = "https://bank.example.com"

        def azure(*args):
            if args[:2] == ("account", "show"):
                return json.dumps({"tenantId": "test-tenant"})
            if args[:3] == ("ad", "signed-in-user", "show"):
                return "test-owner"
            if args[:2] == ("webapp", "create"):
                return json.dumps({"defaultHostName": "bank.example.com",
                                   "identity": {"principalId": "test-identity"}})
            if args[:2] == ("account", "get-access-token"):
                return json.dumps({"accessToken": "synthetic-token"})
            return "{}"

        cases = [(approved, "test-assertion-client", [deploy_web.AUTH_ASSERTION_SETTING])
                 for approved in ([origin], [], ["https://foreign.example.com"], ["*"])]
        cases += [([origin], "wrong-identity-client", [deploy_web.AUTH_ASSERTION_SETTING]),
                  ([origin], "test-assertion-client", [])]
        for approved, assertion_client, sticky in cases:
            with self.subTest(approved=approved, assertion_client=assertion_client, sticky=sticky):
                deployed = {}
                app_settings = {"BANKING_AUTH_SECRET": "synthetic-secret"}

                def arm(request, timeout):
                    if "/config/authsettingsV2?" in request.full_url:
                        if request.method == "PUT":
                            deployed.update(json.loads(request.data)["properties"])
                        else:
                            self.assertEqual(deployed["login"]["allowedExternalRedirectUrls"], [origin])
                            self.assertTrue(deployed["platform"]["enabled"])
                            self.assertTrue(deployed["globalValidation"]["requireAuthentication"])
                            self.assertTrue(deployed["httpSettings"]["requireHttps"])
                            registration = deployed["identityProviders"]["azureActiveDirectory"]["registration"]
                            self.assertEqual(registration["clientSecretSettingName"],
                                             deploy_web.AUTH_ASSERTION_SETTING)
                            identities = deployed["identityProviders"]["azureActiveDirectory"][
                                "validation"]["defaultAuthorizationPolicy"]["allowedPrincipals"]["identities"]
                            self.assertEqual(identities, ["test-owner"])
                            deployed["login"]["allowedExternalRedirectUrls"] = approved
                        body = {"properties": deployed}
                    elif "/config/slotConfigNames?" in request.full_url:
                        body = {"properties": {"appSettingNames": sticky}}
                    else:
                        if request.method == "PUT":
                            app_settings.update(json.loads(request.data)["properties"])
                            self.assertEqual(app_settings[deploy_web.AUTH_ASSERTION_SETTING],
                                             "test-assertion-client")
                        elif deploy_web.AUTH_ASSERTION_SETTING in app_settings:
                            app_settings[deploy_web.AUTH_ASSERTION_SETTING] = assertion_client
                        body = {"properties": app_settings}
                    response = Mock()
                    response.__enter__ = Mock(return_value=io.BytesIO(json.dumps(body).encode()))
                    response.__exit__ = Mock(return_value=False)
                    return response

                with patch.object(deploy_web, "load_config", return_value=config), \
                        patch.object(deploy_web, "read_environment", return_value=env), \
                        patch.object(deploy_web, "ensure_auth_identity", return_value="test-assertion-client"), \
                        patch.object(deploy_web, "azure", side_effect=azure), \
                        patch.object(deploy_web, "save_local_value"), \
                        patch.object(deploy_web.urllib.request, "urlopen", side_effect=arm), \
                        patch.object(deploy_web, "package_web", return_value=Path("synthetic.zip")) as package, \
                        contextlib.redirect_stdout(io.StringIO()):
                    if assertion_client != "test-assertion-client" or not sticky:
                        with self.assertRaisesRegex(RuntimeError, "authentication switch refused"):
                            deploy_web.main()
                        self.assertEqual(deployed, {})
                        package.assert_not_called()
                    elif approved == [origin]:
                        deploy_web.main()
                        package.assert_called_once()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "Authentication readback failed"):
                            deploy_web.main()
                        package.assert_not_called()

    def test_banking_identity_override_preserves_procurement_subscription_and_tenant(self):
        self.path.write_text(
            "AZURE_SUBSCRIPTION_ID=commerce-subscription\nAZURE_TENANT_ID=commerce-tenant\n"
            "BANKING_AZURE_SUBSCRIPTION_ID=banking-subscription\n"
            "BANKING_AZURE_TENANT_ID=banking-tenant\n", encoding="utf-8")
        self.assertEqual(settings.load_config()["subscription_id"], "commerce-subscription")
        self.assertEqual(settings.load_config()["tenant_id"], "commerce-tenant")
        self.assertEqual(settings.load_config("banking")["subscription_id"], "banking-subscription")
        self.assertEqual(settings.load_config("banking")["tenant_id"], "banking-tenant")
        settings.save_local_value("BANKING_AZURE_SUBSCRIPTION_ID", "")
        settings.save_local_value("BANKING_AZURE_TENANT_ID", "")
        self.assertEqual(settings.load_config("banking")["subscription_id"], "commerce-subscription")
        self.assertEqual(settings.load_config("banking")["tenant_id"], "commerce-tenant")

    def test_save_version_preserves_other_settings_without_public_json(self):
        self.path.write_text("# Keep comment\nOTHER=literal\nBANKING_VOICE_AGENT_VERSION=\n", encoding="utf-8")
        settings.save_local_value("BANKING_VOICE_AGENT_VERSION", "3")
        settings.save_local_value("NEW_SETTING", 'quoted "value"')
        self.assertIn("# Keep comment", self.path.read_text())
        self.assertEqual(settings.read_environment()["OTHER"], "literal")
        self.assertEqual(settings.read_environment()["BANKING_VOICE_AGENT_VERSION"], "3")
        self.assertEqual(settings.read_environment()["NEW_SETTING"], 'quoted "value"')

    def test_survey_is_optional_and_validated(self):
        self.assertIsNone(settings.survey_url())
        for value in ("http://example.com", "javascript:alert(1)", "https://user:password@example.com"):
            with patch.dict("os.environ", {"DEMO_SURVEY_URL": value}):
                with self.assertRaises(ValueError):
                    settings.survey_url()
        with patch.dict("os.environ", {"DEMO_SURVEY_URL": "https://example.com/survey"}):
            self.assertEqual(settings.survey_url(), "https://example.com/survey")

    def test_survey_operation_is_explicit_when_unconfigured(self):
        from banking import Bank
        bank = Bank(Path(self.temp.name) / "bank.db")
        session = bank.start("leyla")["session"]
        result = bank.action(session, "survey")
        self.assertIn("error", result)
        self.assertNotIn("url", result)

    def test_invalid_state_does_not_invent_deployment_success(self):
        self.path.write_text("BANKING_PROJECT_STATE=private-value\n", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            settings.load_config("banking")
        self.assertNotIn("private-value", str(caught.exception))

    def test_publication_detects_private_values_without_returning_them(self):
        private = private_values({"AZURE_SUBSCRIPTION_ID": "synthetic-private-value",
                                  "BANKING_REGION": "swedencentral"})
        self.assertEqual(private, {"synthetic-private-value"})
        self.assertEqual(private_values({"BANKING_AZURE_SUBSCRIPTION_ID": "banking-private-subscription",
                                         "BANKING_AZURE_TENANT_ID": "banking-private-tenant"}),
                         {"banking-private-subscription", "banking-private-tenant"})
        for key in ("BANKING_WEB_APP", "BANKING_WEB_URL", "BANKING_WEB_CLIENT_ID",
                    "CLOUD_EXPECTED_ORIGIN", "CLOUD_AUTH_TENANT", "CLOUD_ALLOWED_PRINCIPAL_IDS",
                    "CLOUD_REVIEWER_IDS", "CLOUD_CSRF_SECRET", "BANKING_AUTH_SECRET"):
            self.assertEqual(private_values({key: "synthetic-private-value"}), {"synthetic-private-value"})
        self.assertEqual(findings("Setting synthetic-private-value", private), ["local deployment value"])
        self.assertEqual(findings("Only public defaults", private), [])
        self.assertTrue(private_path(".env"))
        self.assertTrue(private_path(".env.production"))
        self.assertTrue(private_path("data\\audit.json"))
        self.assertFalse(private_path(".env.example"))
        self.assertFalse(private_path("infra\\banking-foundry.json"))

    def test_deployment_fails_before_mutation_if_settings_are_missing(self):
        from deploy_banking import main
        with patch("deploy_banking.azure") as azure:
            with self.assertRaises(ValueError):
                main()
            azure.assert_not_called()

    def test_publication_checks_reachable_blobs_even_when_working_file_is_clean(self):
        from check_publication import main
        root = Path(self.temp.name)
        (root / "public.txt").write_text("Public content", encoding="utf-8")
        responses = ["public.txt\0", "old-object public.txt\n", "old-object blob 23\n",
                     "synthetic-private-value", "public.txt\n", ""]
        output = io.StringIO()
        with patch("check_publication.ROOT", root), patch("check_publication.git", side_effect=responses), \
                patch("check_publication.read_environment", return_value={
                    "AZURE_SUBSCRIPTION_ID": "synthetic-private-value"}), contextlib.redirect_stdout(output):
            self.assertEqual(main(), 1)
        self.assertIn("branch/remote history: local deployment value", output.getvalue())
        self.assertNotIn("synthetic-private-value", output.getvalue())

    def test_provisioned_state_is_not_equated_with_connected_voice(self):
        from deploy_banking import main
        values = {
            "AZURE_SUBSCRIPTION_ID": str(UUID(int=1)),
            "AZURE_TENANT_ID": str(UUID(int=2)),
            "BANKING_RESOURCE_GROUP": "test-resource-group",
            "BANKING_FOUNDRY_ACCOUNT": "test-account",
            "BANKING_FOUNDRY_PROJECT": "test-project",
            "BANKING_FOUNDRY_ENDPOINT": "https://test-account.services.ai.azure.com/api/projects/test-project",
            "BANKING_REGION": "swedencentral",
            "BANKING_MODEL_DEPLOYMENT": "test-mini",
        }
        with patch.dict("os.environ", values), patch("deploy_banking.azure", side_effect=[
                values["AZURE_TENANT_ID"], "", "", "Failed"]), patch("deploy_banking.Foundry") as client:
            with self.assertRaisesRegex(RuntimeError, "Failed"):
                main()
            client.assert_not_called()
        self.assertEqual(settings.read_environment()["BANKING_PROJECT_STATE"], "Failed")

    def test_failed_deployment_refreshes_pending_state_without_retry_or_agent_call(self):
        from deploy_banking import main
        config = {
            "subscription_id": str(UUID(int=1)), "tenant_id": str(UUID(int=2)),
            "resource_group": "test-resource-group", "account_name": "test-account",
            "project_name": "test-project", "region": "swedencentral",
            "model_deployment": "test-mini",
            "project_endpoint": "https://test-account.services.ai.azure.com/api/projects/test-project",
        }
        self.path.write_text("BANKING_PROJECT_STATE=Succeeded\n", encoding="utf-8")
        for state in ("Accepted", "Failed"):
            with self.subTest(state=state), patch("deploy_banking.load_config", return_value=config), \
                    patch("deploy_banking.azure", side_effect=[
                        config["tenant_id"], "", RuntimeError("Gateway error; correlation test-id"), state
                    ]) as azure, patch("deploy_banking.Foundry") as client:
                with self.assertRaisesRegex(RuntimeError, f"test-id.*\\nLive project state: {state}"):
                    main()
                self.assertEqual(azure.call_count, 4)
                self.assertEqual(sum(call.args[:3] == ("deployment", "group", "create")
                                     for call in azure.call_args_list), 1)
                client.assert_not_called()
                self.assertEqual(settings.read_environment()["BANKING_PROJECT_STATE"], state)

    def test_deployment_and_status_errors_are_both_reported_without_inventing_state(self):
        from deploy_banking import main
        config = {
            "subscription_id": str(UUID(int=1)), "tenant_id": str(UUID(int=2)),
            "resource_group": "test-resource-group", "account_name": "test-account",
            "project_name": "test-project", "region": "swedencentral",
            "model_deployment": "test-mini",
            "project_endpoint": "https://test-account.services.ai.azure.com/api/projects/test-project",
        }
        with patch("deploy_banking.load_config", return_value=config), \
                patch("deploy_banking.azure", side_effect=[
                    config["tenant_id"], "", RuntimeError("Deployment timed out"),
                    RuntimeError("Status endpoint unavailable")
                ]), patch("deploy_banking.Foundry") as client:
            with self.assertRaises(RuntimeError) as caught:
                main()
            self.assertIn("Deployment timed out", str(caught.exception))
            self.assertIn("Status endpoint unavailable", str(caught.exception))
            self.assertNotIn("BANKING_PROJECT_STATE", settings.read_environment())
            client.assert_not_called()

    def test_infrastructure_provisions_model_before_project_like_official_starter(self):
        template = json.loads((settings.ROOT / "infra" / "banking-foundry.json").read_text())
        resources = {resource["type"]: resource for resource in template["resources"]}
        account = resources["Microsoft.CognitiveServices/accounts"]
        model = resources["Microsoft.CognitiveServices/accounts/deployments"]
        project = resources["Microsoft.CognitiveServices/accounts/projects"]
        self.assertTrue(account["properties"]["disableLocalAuth"])
        self.assertIn("Microsoft.CognitiveServices/accounts'", model["dependsOn"][0])
        self.assertIn("Microsoft.CognitiveServices/accounts/deployments'", project["dependsOn"][0])
        self.assertNotIn("projects", model["dependsOn"][0])


class CloudDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "subscription_id": str(UUID(int=1)), "tenant_id": str(UUID(int=2)),
            "resource_group": "test-resource-group", "project_name": "test-project",
            "region": "swedencentral", "model_deployment": "private-model-test",
            "voice_agent_name": "private-voice-test",
        }

    def test_manifest_resolves_private_names_without_changing_public_source(self):
        from deploy_cloud import render_manifest
        path = settings.ROOT / "cloud" / "azure.yaml"
        original = path.read_text(encoding="utf-8")
        rendered = render_manifest(self.config)
        self.assertIn('"private-model-test"', rendered)
        self.assertIn('"private-voice-test"', rendered)
        self.assertIn("$schema=", rendered)
        self.assertNotIn("${", rendered)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertNotIn("private-model-test", original)

    def test_missing_setting_fails_before_azure_or_azd_calls(self):
        import deploy_cloud
        with patch("deploy_cloud.load_config", return_value={}), \
                patch("deploy_cloud.azure") as azure, patch("deploy_cloud.azd") as azd:
            with self.assertRaises(ValueError):
                deploy_cloud.main([])
            azure.assert_not_called()
            azd.assert_not_called()

    def test_azure_speech_uses_compatible_multilingual_semantic_vad_in_both_manifests(self):
        from deploy_cloud import render_manifest
        manifest = render_manifest(self.config)
        draft = json.loads((settings.ROOT / "voice-agent.json").read_text(encoding="utf-8"))
        audio_input = draft["definition"]["audio"]["input"]
        self.assertEqual(audio_input["transcription"]["model"], "azure-speech")
        self.assertEqual(audio_input["turn_detection"]["type"], "azure_semantic_vad_multilingual")
        self.assertTrue(audio_input["turn_detection"]["interrupt_response"])
        self.assertIn('"type": "azure_semantic_vad_multilingual"', manifest)
        self.assertNotIn("server_vad", manifest)

    def test_native_profiles_render_model_audio_and_greeting_from_single_source(self):
        from deploy_cloud import render_manifest, voice_definition
        for locale, native_voice in (("az-AZ", "az-AZ-BanuNeural"),
                                     ("ru-RU", "ru-RU-SvetlanaNeural")):
            definition = voice_definition(locale)
            self.assertEqual(definition["model"], "gpt-realtime-2.1")
            self.assertEqual(definition["audio"]["output"]["voice"], native_voice)
            self.assertEqual(definition["audio"]["output"]["voice_locale"],
                             "az-Latn-AZ" if locale == "az-AZ" else locale)
            transcription = definition["audio"]["input"]["transcription"]
            self.assertEqual(transcription["language"], "az" if locale == "az-AZ" else locale)
            self.assertEqual(transcription["model"], "mai-transcribe-2" if locale == "az-AZ" else "azure-speech")
            self.assertGreaterEqual(definition["max_output_tokens"], 2048)
            echo = definition["audio"]["input"]["echo_cancellation"]
            self.assertEqual(echo, {"type": "server_echo_cancellation"})
            rendered = render_manifest(self.config, locale)
            self.assertIn(native_voice, rendered)
            self.assertIn('"tool_choice": "none"', rendered)
            self.assertIn('"speechDurationMs": 200', rendered)
            self.assertIn('maxOutputTokens: 2048', rendered)
            self.assertNotIn("${", rendered)

    def test_readback_refuses_model_voice_token_and_privacy_drift_without_pinning(self):
        import copy
        import deploy_cloud
        expected = deploy_cloud.voice_definition("ru-RU")
        for field, value in (("model", "gpt-realtime"), ("max_output_tokens", 250), ("store", True),
                             ("audio", {"output": {"voice": "alloy"}}), ("greeting", None)):
            changed = copy.deepcopy(expected)
            changed[field] = value
            with self.subTest(field=field), patch("deploy_cloud.Foundry") as client, \
                    patch("deploy_cloud.save_local_value") as save:
                client.return_value.request.return_value = {"data": [{"version": "3", "definition": changed}]}
                with self.assertRaisesRegex(RuntimeError, "Readback"):
                    deploy_cloud.confirm_voice_version(self.config, "ru-RU")
                save.assert_not_called()

    def test_voice_deployment_requires_ready_project_and_confirms_latest_numeric_version(self):
        import deploy_cloud
        expected = deploy_cloud.voice_definition()
        draft = json.loads((settings.ROOT / "voice-agent.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary, \
                patch("deploy_cloud.ROOT", Path(temporary)), \
                patch("deploy_cloud.render_manifest", return_value="name: test-project\n"), \
                patch("deploy_cloud.load_config", return_value=self.config), \
                patch("deploy_cloud.azure", return_value=self.config["tenant_id"]), \
                patch("deploy_cloud.azd") as azd, \
                patch("deploy_cloud.refresh_project_state", return_value="Creating") as refresh, \
                patch("deploy_cloud.save_local_value") as save, \
                patch("deploy_cloud.Foundry") as client, contextlib.redirect_stdout(io.StringIO()):
            (Path(temporary) / "voice-agent.json").write_text(json.dumps(draft), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "ready"):
                deploy_cloud.main(["--deploy-voice"])
            self.assertFalse(any(call.args[1] == "deploy" for call in azd.call_args_list))
            client.assert_not_called()
            save.assert_not_called()
            refresh.return_value = "Succeeded"
            client.return_value.request.return_value = {"data": [
                {"version": "9", "definition": {"kind": "voice"}},
                {"version": "10", "definition": expected},
            ]}
            self.assertEqual(deploy_cloud.main(["--deploy-voice"]), 0)
            self.assertEqual(sum(call.args[1] == "deploy" for call in azd.call_args_list), 1)
            save.assert_called_once_with("BANKING_VOICE_AGENT_VERSION", "10")
            save.reset_mock()
            client.return_value.request.return_value = {"data": [
                {"version": "11", "definition": {"kind": "voice", "audio": {
                    "input": {"turn_detection": {"type": "server_vad"}},
                }}},
            ]}
            with self.assertRaisesRegex(RuntimeError, "Readback"):
                deploy_cloud.main(["--deploy-voice"])
            save.assert_not_called()

    def test_tenant_mismatch_fails_before_azd(self):
        import deploy_cloud
        with patch("deploy_cloud.load_config", return_value=self.config), \
                patch("deploy_cloud.azure", return_value=str(UUID(int=3))), \
                patch("deploy_cloud.azd") as azd:
            with self.assertRaisesRegex(RuntimeError, "tenant"):
                deploy_cloud.main(["--apply"])
            azd.assert_not_called()

    def test_default_operation_is_preview_and_generated_manifest_is_private(self):
        import deploy_cloud
        with tempfile.TemporaryDirectory() as temporary, \
                patch("deploy_cloud.ROOT", Path(temporary)), \
                patch("deploy_cloud.render_manifest", return_value="name: test-project\n"), \
                patch("deploy_cloud.load_config", return_value=self.config), \
                patch("deploy_cloud.azure", return_value=self.config["tenant_id"]), \
                patch("deploy_cloud.azd") as azd, \
                patch("deploy_cloud.save_local_value") as save:
            self.assertEqual(deploy_cloud.main([]), 0)
            self.assertEqual(azd.call_args.args[1:],
                             ("provision", "--environment", "banking", "--no-prompt", "--preview"))
            self.assertTrue(private_path(str(azd.call_args.args[0] / "azure.yaml")))
            save.assert_not_called()

    def test_failed_apply_refreshes_actual_state_once_without_retry(self):
        import deploy_cloud
        import subprocess
        with tempfile.TemporaryDirectory() as temporary, \
                patch("deploy_cloud.ROOT", Path(temporary)), \
                patch("deploy_cloud.render_manifest", return_value="name: test-project\n"), \
                patch("deploy_cloud.load_config", return_value=self.config), \
                patch("deploy_cloud.azure", return_value=self.config["tenant_id"]), \
                patch("deploy_cloud.azd") as azd, \
                patch("deploy_cloud.refresh_cloud_config", return_value="Accepted") as refresh, \
                patch("deploy_cloud.save_local_value"), \
                patch("deploy_cloud.Foundry") as client, contextlib.redirect_stdout(io.StringIO()):
            def execute(project, *arguments):
                if arguments[0] == "provision":
                    raise subprocess.CalledProcessError(1, ["azd", "provision"])
            azd.side_effect = execute
            with self.assertRaises(subprocess.CalledProcessError):
                deploy_cloud.main(["--apply"])
            self.assertEqual(sum(call.args[1] == "provision" for call in azd.call_args_list), 1)
            refresh.assert_called_once_with(self.config)
            client.assert_not_called()


class VoiceVerificationTests(unittest.TestCase):
    def run_voice(self, *, audio=True, status="completed", marker=True, error=False):
        import verify_voice
        response_id = "requested-response"
        request_marker = UUID(int=42).hex if marker else "unrelated-marker"
        events = [
            {"type": "session.updated"},
            {"type": "response.created", "response": {"id": "greeting"}},
            {"type": "response.output_audio_transcript.delta", "response_id": "greeting",
             "delta": "An automatic greeting must not pass the test."},
            {"type": "response.output_audio.delta", "response_id": "greeting", "delta": "AAEC"},
            {"type": "response.done", "response": {"id": "greeting", "status": "completed"}},
            {"type": "response.created", "response": {
                "id": response_id, "metadata": {verify_voice.MARKER: request_marker},
            }},
            {"type": "response.output_audio_transcript.delta",
             "response_id": response_id, "delta": "Synthetic demo."},
        ]
        if audio:
            events.append({"type": "response.output_audio.delta",
                           "response_id": response_id, "delta": "AAEC"})
        events.append({"type": "response.done", "response": {"id": response_id, "status": status}})
        if error:
            events = [{"type": "error", "error": {"message": "Synthetic service error"}}]
        socket = Mock()
        socket.recv.side_effect = [json.dumps(event) for event in events] + [""]
        module = SimpleNamespace(create_connection=Mock(return_value=socket),
                                 WebSocketTimeoutException=TimeoutError,
                                 WebSocketException=ConnectionError)
        config = {"project_state": "Succeeded", "voice_agent_version": "2",
                  "project_endpoint": "https://example.services.ai.azure.com/api/projects/test",
                  "voice_agent_name": "test-voice"}
        self.socket, self.module = socket, module
        with patch.dict("sys.modules", {"websocket": module}), \
                patch("verify_voice.load_config", return_value=config), \
                patch("verify_voice.Foundry") as client, \
                patch("verify_voice.uuid.uuid4", return_value=UUID(int=42)), \
                patch("verify_voice.time.monotonic", return_value=0), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            client.return_value.token.return_value = "synthetic-token"
            client.return_value.request.return_value = {"definition": {"model": "gpt-realtime-2.1"}}
            verify_voice.verify()
        return output.getvalue()

    def test_only_matching_requested_response_counts_and_socket_closes(self):
        output = self.run_voice()
        self.assertIn('"audio_bytes": 3', output)
        self.assertIn('"transcript": "Synthetic demo."', output)
        self.assertNotIn("automatic greeting", output)
        self.assertNotIn("synthetic-token", output)
        self.socket.close.assert_called_once()
        sent = [json.loads(call.args[0]) for call in self.socket.send.call_args_list]
        self.assertEqual([event["type"] for event in sent],
                         ["conversation.item.create", "response.create"])
        self.assertEqual(sent[1]["response"]["metadata"], {
            "foundry_voice_smoke_test_request": UUID(int=42).hex,
        })

    def test_greeting_audio_cannot_mask_missing_requested_audio_or_failed_response(self):
        for kwargs in ({"audio": False}, {"status": "failed"}, {"marker": False}):
            with self.subTest(kwargs=kwargs), self.assertRaises(RuntimeError):
                self.run_voice(**kwargs)
            self.socket.close.assert_called_once()

    def test_service_errors_are_reported_and_socket_closes(self):
        with self.assertRaisesRegex(RuntimeError, "Synthetic service error"):
            self.run_voice(error=True)
        self.socket.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
