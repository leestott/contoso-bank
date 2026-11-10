"""Deploy parameterized banking infrastructure using local .env settings."""

import re
import shutil
import subprocess
from uuid import UUID

from foundry import Foundry, FoundryError, ROOT
from settings import load_config, save_local_value


def azure(*arguments):
    executable = shutil.which("az")
    if not executable:
        raise RuntimeError("Azure CLI is required; install it and sign in.")
    try:
        result = subprocess.run([executable, *arguments, "--only-show-errors"],
                                capture_output=True, text=True, timeout=1800, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Azure CLI timed out. Check deployment state before retrying.") from None
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Azure CLI failed without diagnostics.")
    return result.stdout.strip()


def refresh_project_state(config):
    state = azure("cognitiveservices", "account", "project", "show",
                  "--subscription", config["subscription_id"],
                  "--resource-group", config["resource_group"],
                  "--name", config["account_name"], "--project-name", config["project_name"],
                  "--query", "properties.provisioningState", "-o", "tsv")
    if state not in {"Unknown", "Creating", "Accepted", "Succeeded", "Failed", "Deleting"}:
        raise RuntimeError("Azure did not return a recognized project provisioning state.")
    save_local_value("BANKING_PROJECT_STATE", state)
    return state


def main():
    config = load_config("banking")
    for key in ("subscription_id", "tenant_id"):
        try:
            UUID(config[key])
        except ValueError:
            raise ValueError(f"Configure a valid {key} in your local .env.") from None
    for key in ("resource_group", "account_name", "project_name", "region", "model_deployment"):
        if config[key] == "Not configured" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,89}", config[key]):
            raise ValueError(f"Configure a valid banking {key} in your local .env.")
    expected_endpoint = f"https://{config['account_name']}.services.ai.azure.com/api/projects/{config['project_name']}"
    if config["project_endpoint"].rstrip("/") != expected_endpoint:
        raise ValueError("BANKING_FOUNDRY_ENDPOINT must match the configured banking account and project.")
    subscription = config["subscription_id"]
    if azure("account", "show", "--subscription", subscription,
             "--query", "tenantId", "-o", "tsv").lower() != config["tenant_id"].lower():
        raise ValueError("The selected Azure subscription belongs to a different tenant.")
    azure("group", "create", "--subscription", subscription,
          "--name", config["resource_group"], "--location", config["region"], "-o", "none")
    try:
        azure("deployment", "group", "create", "--subscription", subscription,
              "--resource-group", config["resource_group"], "--name", "banking-foundry",
              "--template-file", str(ROOT / "infra" / "banking-foundry.json"),
              "--mode", "Incremental", "--parameters",
              f"location={config['region']}", f"accountName={config['account_name']}",
              f"projectName={config['project_name']}", f"modelDeployment={config['model_deployment']}",
              "-o", "none")
    except RuntimeError as deployment_error:
        try:
            state = refresh_project_state(config)
        except (RuntimeError, ValueError, OSError) as status_error:
            raise RuntimeError(
                f"{deployment_error}\nProject status readback also failed: {status_error}\n"
                "Deployment is not verified. Check Azure before retrying.") from None
        raise RuntimeError(
            f"{deployment_error}\nLive project state: {state}. "
            "Deployment is not verified; no voice readiness is claimed. "
            "Check Azure deployment operations before retrying.") from None
    state = refresh_project_state(config)
    if state != "Succeeded":
        raise RuntimeError(f"Banking project is {state}; no voice readiness is claimed.")
    Foundry(config).request("GET", "/agents?api-version=v1")
    print("Project provisioning and agent API access confirmed. Voice is not yet verified.")


if __name__ == "__main__":
    try:
        main()
    except (FoundryError, ValueError, RuntimeError, OSError) as exc:
        print(str(exc))
        raise SystemExit(1)
