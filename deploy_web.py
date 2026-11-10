"""Deploy the HTTPS banking app with tenant-restricted App Service authentication."""

import hashlib
import json
import secrets
import urllib.request
import zipfile

from deploy_banking import azure
from settings import ROOT, load_config, read_environment, save_local_value

AUTH_ASSERTION_SETTING = "OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID"


def ensure_auth_identity(config, name, client_id):
    tenant = azure("account", "show", "--query", "tenantId", "-o", "tsv")
    if tenant.lower() != config["tenant_id"].lower():
        raise RuntimeError("Select the banking Azure tenant before provisioning web sign-in.")
    scope = ["--subscription", config["subscription_id"], "--resource-group", config["resource_group"]]
    identity = json.loads(azure(
        "identity", "create", *scope, "--name", name + "-signin",
        "--location", config["region"], "-o", "json"))
    expected = {
        "name": name + "-signin",
        "issuer": f"https://login.microsoftonline.com/{config['tenant_id']}/v2.0",
        "subject": identity["principalId"],
        "audiences": ["api://AzureADTokenExchange"],
    }
    app_id = azure("ad", "app", "show", "--id", client_id, "--query", "id", "-o", "tsv")
    credentials = json.loads(azure("ad", "app", "federated-credential", "list",
                                   "--id", app_id, "-o", "json"))
    existing = next((item for item in credentials if item["name"] == expected["name"]), None)
    if existing is None:
        parameters = ROOT / ".azure" / (name + "-signin-federation.json")
        parameters.parent.mkdir(parents=True, exist_ok=True)
        parameters.write_text(json.dumps(expected), encoding="utf-8")
        existing = json.loads(azure("ad", "app", "federated-credential", "create",
                                   "--id", app_id, "--parameters", str(parameters), "-o", "json"))
    readback = json.loads(azure("ad", "app", "federated-credential", "show", "--id", app_id,
                                "--federated-credential-id", existing["id"], "-o", "json"))
    if any(readback.get(key) != value for key, value in expected.items()):
        raise RuntimeError("The web sign-in federated credential does not match the dedicated identity.")
    attached = json.loads(azure("webapp", "identity", "assign", *scope, "--name", name,
                               "--identities", "[system]", identity["id"], "-o", "json"))
    if ("SystemAssigned" not in attached.get("type", "")
            or identity["id"].lower() not in {
                key.lower() for key in attached.get("userAssignedIdentities", {})}):
        raise RuntimeError("Web sign-in identity assignment did not preserve the system identity.")
    return identity["clientId"]


def authentication_settings(client_id, tenant_id, identities, origin):
    return {
        "platform": {"enabled": True, "runtimeVersion": "~1"},
        "globalValidation": {"requireAuthentication": True, "unauthenticatedClientAction": "RedirectToLoginPage",
                             "redirectToProvider": "azureactivedirectory"},
        "identityProviders": {"azureActiveDirectory": {
            "enabled": True, "registration": {"clientId": client_id,
                "clientSecretSettingName": AUTH_ASSERTION_SETTING,
                "openIdIssuer": f"https://login.microsoftonline.com/{tenant_id}/v2.0"},
            "validation": {"allowedAudiences": [client_id, "api://" + client_id],
                           "defaultAuthorizationPolicy": {"allowedPrincipals": {"identities": identities}}},
        }},
        "login": {"tokenStore": {"enabled": True}, "allowedExternalRedirectUrls": [origin]},
        "httpSettings": {"requireHttps": True},
    }


def package_web():
    package = ROOT / ".azure" / "banking-web.zip"
    package.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in ("cloud_app.py", "voice_bridge.py", "hosted_banking.py", "banking.py",
                         "gateway.py", "commerce.py", "foundry.py", "transcripts.py", "settings.py", "requirements.txt"):
            archive.write(ROOT / filename, filename)
        for filename in ("banking.html", "banking.js", "banking.css", "style.css", "voice.js", "pcm-worklet.js"):
            archive.write(ROOT / "web" / filename, "web/" + filename)
    return package


def main():
    c = load_config("banking")
    env = read_environment()
    if c["project_state"] != "Succeeded" or not c["voice_agent_version"]:
        raise RuntimeError("A ready banking project and confirmed native voice version are required.")
    if any(not env.get("BANKING_HOSTED_" + role + "_VERSION") for role in
           ("ORCHESTRATOR", "CARD", "LENDING", "PAYMENTS")):
        raise RuntimeError("Confirm all four hosted deployments before deploying the banking app.")
    for filename in ("banking.html", "banking.js", "banking.css", "style.css", "voice.js", "pcm-worklet.js"):
        if not (ROOT / "web" / filename).is_file():
            raise RuntimeError("A required banking web asset is missing: " + filename)
    selected = json.loads(azure("account", "show", "--subscription", c["subscription_id"], "-o", "json"))
    if selected["tenantId"].lower() != c["tenant_id"].lower():
        raise RuntimeError("Selected Azure identity does not match the banking tenant.")
    owner = azure("ad", "signed-in-user", "show", "--query", "id", "-o", "tsv")
    # The project managed identity used by the governed Toolbox connection may call /gateway/mcp only.
    gateway = env.get("BANKING_GATEWAY_PRINCIPAL_ID", "").strip()
    identities = [owner] + ([gateway] if gateway else [])
    if gateway and gateway.lower() == owner.lower():
        raise RuntimeError("The gateway identity must differ from the presenter identity.")
    suffix = hashlib.sha256((c["subscription_id"] + c["project_name"]).encode()).hexdigest()[:10]
    name, plan = "bank-" + suffix, "plan-bank-" + suffix
    resource = (f"/subscriptions/{c['subscription_id']}/resourceGroups/{c['resource_group']}"
                f"/providers/Microsoft.Web/sites/{name}")
    scope = ["--subscription", c["subscription_id"], "--resource-group", c["resource_group"]]
    azure("appservice", "plan", "create", *scope, "--name", plan, "--location", c["region"],
          "--is-linux", "--sku", "B1", "--number-of-workers", "1", "-o", "none")
    site = json.loads(azure("webapp", "create", *scope, "--plan", plan, "--name", name,
                           "--runtime", "PYTHON:3.13", "--https-only", "true",
                           "--assign-identity", "--basic-auth", "Disabled", "-o", "json"))
    url = "https://" + site["defaultHostName"]
    save_local_value("BANKING_WEB_APP", name)
    save_local_value("BANKING_WEB_URL", url)
    client_id = env.get("BANKING_WEB_CLIENT_ID")
    if not client_id:
        app = json.loads(azure("ad", "app", "create", "--display-name", c["project_name"] + " banking staff",
                               "--sign-in-audience", "AzureADMyOrg", "--web-redirect-uris",
                               url + "/.auth/login/aad/callback", "--enable-id-token-issuance", "true",
                               "-o", "json"))
        client_id = app["appId"]
        save_local_value("BANKING_WEB_CLIENT_ID", client_id)
        azure("ad", "sp", "create", "--id", client_id, "-o", "none")
    arm_token = json.loads(azure("account", "get-access-token", "--subscription", c["subscription_id"],
                                "--resource", "https://management.azure.com/", "-o", "json"))["accessToken"]

    def arm(method, path, body=None):
        request = urllib.request.Request(
            "https://management.azure.com" + resource + path + "?api-version=2024-11-01",
            data=json.dumps(body).encode() if body is not None else None, method=method,
            headers={"Authorization": "Bearer " + arm_token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)

    current = arm("POST", "/config/appsettings/list")["properties"]
    assertion_client_id = ensure_auth_identity(c, name, client_id)
    for key, value in env.items():
        if key.startswith("BANKING_") and not key.startswith("BANKING_WEB_"):
            current[key] = value
    if gateway and env.get("BANKING_GATEWAY_TOOLBOX") and env.get("BANKING_GATEWAY_TOOLBOX_VERSION"):
        current["BANKING_GATEWAY_TOOLBOX_MCP"] = (
            f"{c['project_endpoint'].rstrip('/')}/toolboxes/{env['BANKING_GATEWAY_TOOLBOX']}/versions/"
            f"{env['BANKING_GATEWAY_TOOLBOX_VERSION']}/mcp?api-version=v1")
        current["BANKING_HOSTED_ISOLATION"] = "delegated"
    current.update({
        AUTH_ASSERTION_SETTING: assertion_client_id, "CLOUD_EXPECTED_ORIGIN": url,
        "CLOUD_ALLOWED_PRINCIPAL_IDS": owner, "CLOUD_REVIEWER_IDS": owner,
        "CLOUD_GATEWAY_PRINCIPAL_IDS": gateway,
        "CLOUD_AUTH_TENANT": c["tenant_id"], "CLOUD_CSRF_SECRET": current.get(
            "CLOUD_CSRF_SECRET") or secrets.token_hex(32),
        "SCM_DO_BUILD_DURING_DEPLOYMENT": "true", "ENABLE_ORYX_BUILD": "true",
        "WEBSITES_PORT": "8000", "PYTHONNOUSERSITE": "1",
    })
    if env.get("DEMO_SURVEY_URL"):
        current["DEMO_SURVEY_URL"] = env["DEMO_SURVEY_URL"]
    arm("PUT", "/config/appsettings", {"properties": current})
    azure("webapp", "config", "appsettings", "set", *scope, "--name", name,
          "--slot-settings", AUTH_ASSERTION_SETTING + "=" + assertion_client_id, "-o", "none")
    staged = arm("POST", "/config/appsettings/list")["properties"]
    sticky = arm("GET", "/config/slotConfigNames")["properties"].get("appSettingNames", [])
    if staged.get(AUTH_ASSERTION_SETTING) != assertion_client_id or AUTH_ASSERTION_SETTING not in sticky:
        raise RuntimeError("Managed identity sign-in settings readback failed; authentication switch refused.")
    arm("PUT", "/config/authsettingsV2", {
        "properties": authentication_settings(client_id, c["tenant_id"], identities, url)})
    auth = arm("GET", "/config/authsettingsV2")["properties"]
    provider = auth.get("identityProviders", {}).get("azureActiveDirectory", {})
    if (not auth["platform"]["enabled"] or not auth["globalValidation"]["requireAuthentication"]
            or not auth.get("httpSettings", {}).get("requireHttps")
            or auth.get("login", {}).get("allowedExternalRedirectUrls") != [url]
            or provider.get("registration", {}).get("clientId") != client_id
            or provider.get("registration", {}).get("clientSecretSettingName") != AUTH_ASSERTION_SETTING
            or provider.get("registration", {}).get("openIdIssuer") !=
               f"https://login.microsoftonline.com/{c['tenant_id']}/v2.0"
            or provider.get("validation", {}).get("defaultAuthorizationPolicy", {}).get(
                "allowedPrincipals", {}).get("identities") != identities):
        raise RuntimeError("Authentication readback failed; code deployment is refused.")
    project_scope = (f"/subscriptions/{c['subscription_id']}/resourceGroups/{c['resource_group']}"
                     f"/providers/Microsoft.CognitiveServices/accounts/{c['account_name']}"
                     f"/projects/{c['project_name']}")
    azure("role", "assignment", "create", "--subscription", c["subscription_id"],
          "--assignee-object-id", site["identity"]["principalId"],
          "--assignee-principal-type", "ServicePrincipal", "--role", "Foundry User",
          "--scope", project_scope, "-o", "none")
    azure("webapp", "config", "set", *scope, "--name", name, "--always-on", "true",
          "--startup-file", "python -m uvicorn cloud_app:app --host 0.0.0.0 --port 8000 --ws-max-size 65536",
          "-o", "none")
    package = package_web()
    result = azure("webapp", "deploy", *scope, "--name", name, "--src-path", str(package),
                   "--type", "zip", "--async", "true", "--track-status", "false", "-o", "json")
    print(result)
    print("Source deployment submitted:", url + "/banking")
    print("Verify deployment completion, runtime startup and sign-in before claiming application readiness.")


if __name__ == "__main__":
    main()
