"""Provision the governed banking MCP gateway toolbox.

The toolbox reaches the App Service ``/gateway/mcp`` endpoint with the Foundry
project's managed identity. Easy Auth validates that token and the app accepts
only the configured gateway principal on that single route. The gateway can
execute only a single-use grant that the authenticated broker issued for one
customer session, operation and server-held payload.
"""

import json

from foundry import Foundry
from provision_iq import http, token
from settings import load_config, read_environment, save_local_value

CONNECTION, TOOLBOX = "bank-gateway-mcp", "bank-gateway"


def main():
    config, env = load_config("banking"), read_environment()
    url, client_id = env.get("BANKING_WEB_URL", "").rstrip("/"), env.get("BANKING_WEB_CLIENT_ID", "")
    if not url.startswith("https://") or not client_id:
        raise RuntimeError("Deploy the authenticated banking web app first.")
    arm = token("https://management.azure.com", config)
    account = (f"https://management.azure.com/subscriptions/{config['subscription_id']}/resourceGroups/"
               f"{config['resource_group']}/providers/Microsoft.CognitiveServices/accounts/{config['account_name']}")
    project = http("GET", f"{account}/projects/{config['project_name']}?api-version=2025-10-01-preview", arm)
    principal = (project.get("identity") or {}).get("principalId")
    if not principal:
        raise RuntimeError("The Foundry project has no system-assigned managed identity.")
    save_local_value("BANKING_GATEWAY_PRINCIPAL_ID", principal)
    target = url + "/gateway/mcp"
    http("PUT", f"{account}/projects/{config['project_name']}/connections/{CONNECTION}"
                f"?api-version=2025-10-01-preview", arm,
         {"properties": {"category": "RemoteTool", "authType": "ProjectManagedIdentity", "target": target,
                         "audience": client_id, "isSharedToAll": True,
                         "metadata": {"ApiType": "Azure"}}})
    if env.get("BANKING_GATEWAY_TOOLBOX_VERSION"):
        print(json.dumps({"toolbox": TOOLBOX, "version": env["BANKING_GATEWAY_TOOLBOX_VERSION"],
                          "connection": CONNECTION, "updated": "connection"}))
        return
    version = Foundry(config).request("POST", f"/toolboxes/{TOOLBOX}/versions?api-version=v1", {
        "description": "Governed banking gateway: executes only single-use broker grants.",
        "tools": [{"type": "mcp", "server_label": "bank_gateway", "server_url": target,
                   "project_connection_id": CONNECTION, "require_approval": "never",
                   "allowed_tools": ["execute_banking_operation"]}],
    })
    number = str(version.get("version", ""))
    save_local_value("BANKING_GATEWAY_TOOLBOX", TOOLBOX)
    save_local_value("BANKING_GATEWAY_TOOLBOX_VERSION", number)
    print(json.dumps({"toolbox": TOOLBOX, "version": number, "connection": CONNECTION}))


if __name__ == "__main__":
    main()
