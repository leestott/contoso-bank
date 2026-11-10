"""Local .env configuration; exported environment variables take precedence."""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"


def read_environment(path=None, environ=None):
    values = {}
    source = Path(path) if path is not None else ENV_FILE
    if source.exists():
        for number, line in enumerate(source.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, raw = line.partition("=")
            key, raw = key.strip(), raw.strip()
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in values:
                raise ValueError(f"Invalid or duplicate .env assignment at line {number}.")
            try:
                if raw.startswith('"'):
                    value, end = json.JSONDecoder().raw_decode(raw)
                    if not isinstance(value, str) or (raw[end:].strip() and not raw[end:].lstrip().startswith("#")):
                        raise ValueError()
                elif raw.startswith("'"):
                    end = raw.find("'", 1)
                    if end < 0 or (raw[end + 1:].strip() and not raw[end + 1:].lstrip().startswith("#")):
                        raise ValueError()
                    value = raw[1:end]
                else:
                    value = raw.split(" #", 1)[0].rstrip()
            except ValueError:
                raise ValueError(f"Invalid .env value at line {number}.") from None
            values[key] = value
    values.update(os.environ if environ is None else environ)
    return values


def load_config(kind="commerce"):
    if kind not in {"commerce", "banking"}:
        raise ValueError("Unknown configuration kind.")
    env = read_environment()
    prefix = kind.upper()
    config = {
        "subscription_id": env.get(f"{prefix}_AZURE_SUBSCRIPTION_ID") or env.get("AZURE_SUBSCRIPTION_ID", ""),
        "tenant_id": env.get(f"{prefix}_AZURE_TENANT_ID") or env.get("AZURE_TENANT_ID", ""),
        "resource_group": env.get(f"{prefix}_RESOURCE_GROUP", ""),
        "account_name": env.get(f"{prefix}_FOUNDRY_ACCOUNT", ""),
        "project_name": env.get(f"{prefix}_FOUNDRY_PROJECT", "") or "Not configured",
        "project_endpoint": env.get(f"{prefix}_FOUNDRY_ENDPOINT", ""),
        "model_deployment": env.get(f"{prefix}_MODEL_DEPLOYMENT", ""),
    }
    if kind == "commerce":
        config.update(agent_name=env.get("COMMERCE_AGENT_NAME", ""),
                      agent_version=env.get("COMMERCE_AGENT_VERSION", "1"))
    else:
        state = env.get("BANKING_PROJECT_STATE", "Unknown")
        if state not in {"Unknown", "Creating", "Accepted", "Succeeded", "Failed", "Deleting"}:
            raise ValueError("BANKING_PROJECT_STATE is not a supported provisioning state.")
        config.update(region=env.get("BANKING_REGION", "") or "Not configured",
                      project_state=state,
                      voice_agent_name=env.get("BANKING_VOICE_AGENT_NAME", ""),
                      voice_agent_version=env.get("BANKING_VOICE_AGENT_VERSION", "") or None)
        config["voice_profiles"] = {
            locale: {"name": config["voice_agent_name"] + suffix,
                     "version": env.get(f"BANKING_VOICE{key}_AGENT_VERSION") or None}
            for locale, suffix, key in
            (("en-GB", "", ""), ("az-AZ", "-az", "_AZ"), ("ru-RU", "-ru", "_RU"))
        }
    return config


def survey_url():
    value = read_environment().get("DEMO_SURVEY_URL", "")
    parsed = urlparse(value)
    if value and (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password):
        raise ValueError("DEMO_SURVEY_URL must be an HTTPS URL without credentials.")
    return value or None


def save_local_value(key, value):
    """Persist a confirmed deployment result without changing exported overrides."""
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) or not isinstance(value, str):
        raise ValueError("Invalid local environment setting.")
    read_environment(environ={})
    lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines() if ENV_FILE.exists() else []
    assignment = f"{key}={json.dumps(value)}"
    for index, line in enumerate(lines):
        existing = line.strip().removeprefix("export ").partition("=")[0].strip()
        if existing == key:
            lines[index] = assignment
            break
    else:
        lines.append(assignment)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
