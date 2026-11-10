"""Verify secretless sign-in provisioning without creating Azure resources."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy_web import AUTH_ASSERTION_SETTING, authentication_settings, ensure_auth_identity


class WebAuthenticationDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.config = {"subscription_id": "test-subscription", "resource_group": "test-group",
                       "region": "test-region", "tenant_id": "test-tenant"}
        self.identity = {"id": "/subscriptions/test-subscription/identities/test-bank-signin",
                         "principalId": "test-principal", "clientId": "test-identity-client"}
        self.credential = {
            "id": "test-federation", "name": "test-bank-signin",
            "issuer": "https://login.microsoftonline.com/test-tenant/v2.0",
            "subject": "test-principal", "audiences": ["api://AzureADTokenExchange"],
        }
        self.attached = {"type": "SystemAssigned, UserAssigned",
                         "userAssignedIdentities": {self.identity["id"]: {}}}

    def test_authentication_preserves_the_presenter_and_gateway_boundaries(self):
        identities = ["presenter", "governed-gateway"]
        value = authentication_settings("web-client", "test-tenant", identities, "https://bank.example.com")
        self.assertTrue(value["platform"]["enabled"])
        self.assertTrue(value["globalValidation"]["requireAuthentication"])
        self.assertTrue(value["httpSettings"]["requireHttps"])
        self.assertEqual(value["login"]["allowedExternalRedirectUrls"], ["https://bank.example.com"])
        aad = value["identityProviders"]["azureActiveDirectory"]
        self.assertEqual(aad["registration"]["clientId"], "web-client")
        self.assertEqual(aad["registration"]["clientSecretSettingName"], AUTH_ASSERTION_SETTING)
        self.assertEqual(aad["registration"]["openIdIssuer"],
                         "https://login.microsoftonline.com/test-tenant/v2.0")
        self.assertEqual(aad["validation"]["allowedAudiences"], ["web-client", "api://web-client"])
        self.assertEqual(aad["validation"]["defaultAuthorizationPolicy"]["allowedPrincipals"]["identities"],
                         identities)
        self.assertNotIn("BANKING_AUTH_SECRET", json.dumps(value))

    def test_existing_federation_is_read_back_before_assignment(self):
        with patch("deploy_web.azure", side_effect=[
                "test-tenant", json.dumps(self.identity), "web-app-object", json.dumps([self.credential]),
                json.dumps(self.credential), json.dumps(self.attached)]) as command:
            self.assertEqual(ensure_auth_identity(self.config, "test-bank", "web-client"),
                             self.identity["clientId"])
        calls = [call.args for call in command.call_args_list]
        self.assertEqual(calls[-2][:5], ("ad", "app", "federated-credential", "show", "--id"))
        self.assertEqual(calls[-1][:3], ("webapp", "identity", "assign"))
        self.assertIn("[system]", calls[-1])
        self.assertFalse(any("reset" in call or "role" in call for call in calls))

    def test_new_federation_uses_principal_not_managed_identity_client(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("deploy_web.ROOT", Path(directory)), patch("deploy_web.azure", side_effect=[
                    "test-tenant", json.dumps(self.identity), "web-app-object", "[]", json.dumps(self.credential),
                    json.dumps(self.credential), json.dumps(self.attached)]) as command:
                ensure_auth_identity(self.config, "test-bank", "web-client")
                parameters = Path(directory) / ".azure" / "test-bank-signin-federation.json"
                value = json.loads(parameters.read_text(encoding="utf-8"))
                self.assertEqual(value["subject"], self.identity["principalId"])
                self.assertEqual(value["audiences"], ["api://AzureADTokenExchange"])
                self.assertEqual(command.call_args_list[4].args[:4],
                                 ("ad", "app", "federated-credential", "create"))

    def test_mismatched_federation_refuses_identity_assignment(self):
        for key, invalid in (("subject", "another-principal"), ("issuer", "https://foreign.example.com"),
                             ("audiences", ["another-audience"])):
            with self.subTest(field=key):
                readback = {**self.credential, key: invalid}
                with patch("deploy_web.azure", side_effect=[
                        "test-tenant", json.dumps(self.identity), "web-app-object", json.dumps([self.credential]),
                        json.dumps(readback)]) as command:
                    with self.assertRaisesRegex(RuntimeError, "does not match"):
                        ensure_auth_identity(self.config, "test-bank", "web-client")
                    self.assertEqual(command.call_count, 5)

    def test_system_identity_and_dedicated_signin_identity_are_both_required(self):
        for attached in ({"type": "UserAssigned", "userAssignedIdentities": self.attached["userAssignedIdentities"]},
                         {"type": "SystemAssigned", "userAssignedIdentities": {}}):
            with self.subTest(attached=attached):
                with patch("deploy_web.azure", side_effect=[
                        "test-tenant", json.dumps(self.identity), "web-app-object", json.dumps([self.credential]),
                        json.dumps(self.credential), json.dumps(attached)]):
                    with self.assertRaisesRegex(RuntimeError, "preserve the system identity"):
                        ensure_auth_identity(self.config, "test-bank", "web-client")

    def test_wrong_graph_tenant_is_refused_before_any_mutation(self):
        with patch("deploy_web.azure", return_value="another-tenant") as command:
            with self.assertRaisesRegex(RuntimeError, "Select the banking Azure tenant"):
                ensure_auth_identity(self.config, "test-bank", "web-client")
            self.assertEqual(command.call_count, 1)


if __name__ == "__main__":
    unittest.main()
