"""Deploy four actual code-hosted agents into the configured banking project."""

import hashlib
import json
import shutil

from deploy_cloud import azd
from deploy_banking import azure
from foundry import Foundry
from settings import ROOT, load_config, read_environment, save_local_value

ROLES = ("orchestrator", "card", "lending", "payments")


def main():
    config = load_config("banking")
    if config["project_state"] != "Succeeded":
        raise RuntimeError("The banking project must be provisioned first.")
    if azure("account", "show", "--subscription", config["subscription_id"],
             "--query", "tenantId", "-o", "tsv").lower() != config["tenant_id"].lower():
        raise RuntimeError("Azure CLI is not using the configured banking tenant.")
    target = "|".join(config[key] for key in ("subscription_id", "resource_group", "project_name"))
    suffix = hashlib.sha256(target.encode()).hexdigest()[:12]
    project = ROOT / ".azure" / "banking-hosted" / suffix
    source = project / "src"
    source.mkdir(parents=True, exist_ok=True)
    for name in ("main.py", "requirements.txt"):
        shutil.copyfile(ROOT / "cloud" / "hosted" / name, source / name)
    env = read_environment()
    endpoint = config["project_endpoint"].rstrip("/")
    toolboxes = {}
    for key in ("KNOWLEDGE", "GATEWAY"):
        name, version = env.get(f"BANKING_{key}_TOOLBOX"), env.get(f"BANKING_{key}_TOOLBOX_VERSION")
        if name and version:
            toolboxes[f"BANKING_{key}_TOOLBOX_MCP"] = (
                f"{endpoint}/toolboxes/{name}/versions/{version}/mcp?api-version=v1")
    services = {"foundry": {"host": "azure.ai.project", "endpoint": config["project_endpoint"]}}
    for role in ROLES:
        name = "banking-" + role
        services[name] = {
            "host": "azure.ai.agent", "kind": "hosted", "name": name,
            "language": "python", "project": "src", "uses": ["foundry"],
            "protocols": [{"protocol": "invocations", "version": "2.0.0"}],
            "codeConfiguration": {"runtime": "python_3_13", "entryPoint": "main.py",
                                  "dependencyResolution": "remote_build"},
            "container": {"resources": {"cpu": "0.5", "memory": "1Gi"}},
            # Idle sessions are suspended (compute to zero) and their $HOME restored on next use.
            "sessionConfiguration": {"idleTimeoutSeconds": 300},
            "env": {"BANK_AGENT_ROLE": role, "PYTHONNOUSERSITE": "1",
                    "FOUNDRY_PROJECT_ENDPOINT": config["project_endpoint"],
                    "BANKING_MODEL_DEPLOYMENT": config["model_deployment"],
                    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "false",
                    **(toolboxes if role == "orchestrator" else {})},
        }
    (project / "azure.yaml").write_text(json.dumps({
        "name": "banking-hosted", "infra": {"provider": "microsoft.foundry"}, "services": services,
    }, indent=2), encoding="utf-8")
    azd(project, "config", "set", "auth.useAzCliAuth", "true")
    if not (project / ".azure" / "banking" / ".env").exists():
        azd(project, "env", "new", "banking", "--subscription", config["subscription_id"],
            "--location", config["region"], "--no-prompt")
    for key, value in {
        "AZURE_SUBSCRIPTION_ID": config["subscription_id"], "AZURE_LOCATION": config["region"],
        "AZURE_TENANT_ID": config["tenant_id"], "USE_EXISTING_AI_PROJECT": "true",
        "AZD_AGENT_SKIP_ACR": "true",
        "AZURE_RESOURCE_GROUP": config["resource_group"],
        "AZURE_AI_ACCOUNT_NAME": config["account_name"],
        "AZURE_AI_PROJECT_NAME": config["project_name"],
        "AZURE_AI_PROJECT_ENDPOINT": config["project_endpoint"],
        "FOUNDRY_PROJECT_ENDPOINT": config["project_endpoint"],
        "AZURE_AI_PROJECT_ID": (
            f"/subscriptions/{config['subscription_id']}/resourceGroups/{config['resource_group']}"
            f"/providers/Microsoft.CognitiveServices/accounts/{config['account_name']}"
            f"/projects/{config['project_name']}"),
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": config["model_deployment"],
        "ENABLE_HOSTED_AGENTS": "true", "ENABLE_CAPABILITY_HOST": "false",
    }.items():
        azd(project, "env", "set", key, value)
    client = Foundry(config)
    for role in ROLES:
        name = "banking-" + role
        live = env.get("BANKING_HOSTED_" + role.upper() + "_VERSION")
        azd(project, "deploy", name, "--environment", "banking", "--no-prompt")
        versions = client.request("GET", f"/agents/{name}/versions?api-version=v1")["data"]
        latest = max(versions, key=lambda version: int(version["version"]))
        if latest["definition"]["kind"] != "hosted":
            raise RuntimeError(f"{name} did not read back as an actual hosted agent.")
        if live and live != str(latest["version"]):
            # New versions are candidates: keep live traffic on the verified version until promoted.
            pin(client, name, live)
            save_local_value("BANKING_HOSTED_" + role.upper() + "_CANDIDATE", str(latest["version"]))
            print(f"Created candidate {name} version {latest['version']}; live traffic stays on {live}.")
        else:
            save_local_value("BANKING_HOSTED_" + role.upper() + "_VERSION", str(latest["version"]))
            print(f"Confirmed code-hosted {name}, version {latest['version']}.")


def pin(client, name, version):
    client.request("PATCH", f"/agents/{name}?api-version=v1", {"agent_endpoint": {"version_selector": {
        "version_selection_rules": [{"type": "FixedRatio", "agent_version": str(version),
                                     "traffic_percentage": 100}]}}},
                   {"Content-Type": "application/merge-patch+json"})
    rules = client.request("GET", f"/agents/{name}?api-version=v1")["agent_endpoint"]["version_selector"][
        "version_selection_rules"]
    if [(rule.get("agent_version"), rule.get("traffic_percentage")) for rule in rules] != [(str(version), 100)]:
        raise RuntimeError(f"{name} did not read back pinned to version {version}.")


def promote():
    """Move live traffic to verified candidate versions; prior versions remain for rollback."""
    config, env = load_config("banking"), read_environment()
    client = Foundry(config)
    for role in ROLES:
        candidate = env.get("BANKING_HOSTED_" + role.upper() + "_CANDIDATE")
        if not candidate:
            raise RuntimeError(f"No verified candidate is recorded for banking-{role}.")
        pin(client, "banking-" + role, candidate)
        save_local_value("BANKING_HOSTED_" + role.upper() + "_PREVIOUS",
                         env["BANKING_HOSTED_" + role.upper() + "_VERSION"])
        save_local_value("BANKING_HOSTED_" + role.upper() + "_VERSION", candidate)
        print(f"banking-{role} now serves version {candidate}.")


if __name__ == "__main__":
    import sys
    promote() if sys.argv[1:] == ["--promote"] else main()
