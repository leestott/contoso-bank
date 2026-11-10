"""Materialize private azd state from .env; preview unless --apply is explicit."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess

from deploy_banking import azure, refresh_project_state
from foundry import Foundry, FoundryError
from settings import ROOT, load_config, save_local_value

VOICE_PROFILES = {
    "en-GB": ("", "en-GB-SoniaNeural", "British English"),
    "az-AZ": ("-az", "az-AZ-BanuNeural", "Azerbaijani as spoken in Azerbaijan"),
    "ru-RU": ("-ru", "ru-RU-SvetlanaNeural", "Russian as spoken in Russia"),
}


def voice_definition(locale="en-GB"):
    if locale not in VOICE_PROFILES:
        raise ValueError("Unsupported native voice locale.")
    definition = json.loads((ROOT / "voice-agent.json").read_text(encoding="utf-8"))["definition"]
    definition["instructions"] = definition["instructions"].replace(
        "without restarting.", "without restarting the banking session.")
    _, voice, language = VOICE_PROFILES[locale]
    definition["audio"]["input"]["transcription"]["language"] = locale
    definition["audio"]["output"].update(voice=voice, voice_locale=locale)
    if locale == "az-AZ":
        definition["audio"]["input"]["transcription"] = {"model": "mai-transcribe-2", "language": "az"}
        definition["audio"]["output"]["voice_locale"] = "az-Latn-AZ"
    definition["greeting"]["prompt"] = (
        f"In {language}, briefly disclose that you are an AI, all banking data and money are "
        "synthetic, and a human can be requested. Ask how you can help. "
        "Do not discuss customer data or call tools."
    )
    definition["instructions"] += (
        f" The selected voice profile is {language}. Start and normally continue in this language. "
        "Pronounce local names and AZN amounts naturally in the selected language. "
        "Do not imitate an English accent. If a different language is requested, explain that "
        "the matching native voice requires ending this voice call and selecting that language; "
        "the banking session and its verification must be preserved."
    )
    return definition


def camel_keys(value):
    if isinstance(value, dict):
        return {re.sub(r"_([a-z])", lambda match: match[1].upper(), key): camel_keys(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [camel_keys(item) for item in value]
    return value


def render_manifest(config, locale="en-GB"):
    fields = {
        "BANKING_MODEL_DEPLOYMENT": "model_deployment",
        "BANKING_VOICE_AGENT_NAME": "voice_agent_name",
    }
    for field in ("subscription_id", "tenant_id", "resource_group", "project_name",
                  "region", *fields.values()):
        if not config.get(field):
            raise ValueError(f"Missing banking setting: {field}")
    source = (ROOT / "cloud" / "azure.yaml").read_text(encoding="utf-8")
    for key, field in fields.items():
        source = source.replace("${" + key + "}", json.dumps(config[field]))
    voice = voice_definition(locale)
    audio = camel_keys(voice["audio"])
    output = audio["output"]
    output["voice"] = {"name": output["voice"], "type": output.pop("voiceType"),
                       **({"locale": output["voiceLocale"]} if "voiceLocale" in output else {})}
    output.pop("voiceLocale", None)
    for key, value in {
        "INSTRUCTIONS": voice["instructions"], "TOOLS": voice["tools"],
        "MODEL": voice["model"], "AUDIO": audio, "GREETING": voice["greeting"],
        "MODALITIES": voice["output_modalities"], "MAX_TOKENS": voice["max_output_tokens"],
    }.items():
        source = source.replace("${BANKING_VOICE_" + key + "}", json.dumps(value))
    if re.search(r"\$\{[^}]+\}", source):
        raise ValueError("Unresolved deployment manifest placeholder.")
    return source


def azd(project, *arguments):
    executable = shutil.which("azd")
    if not executable:
        raise RuntimeError("Azure Developer CLI is required.")
    environment = os.environ.copy()
    environment["AZD_CONFIG_DIR"] = str(ROOT / ".azd")
    environment["AZURE_DEV_USER_AGENT"] = "microsoft_foundry_skill"
    subprocess.run(
        [executable, "--cwd", str(project), *arguments],
        env=environment, check=True, timeout=3600,
    )


def refresh_cloud_config(config):
    accounts = json.loads(azure(
        "resource", "list", "--subscription", config["subscription_id"],
        "--resource-group", config["resource_group"],
        "--resource-type", "Microsoft.CognitiveServices/accounts",
        "--query", "[?kind=='AIServices'].name", "-o", "json",
    ))
    if len(accounts) != 1:
        raise RuntimeError("Expected exactly one Foundry account in the banking resource group.")
    account = accounts[0]
    save_local_value("BANKING_FOUNDRY_ACCOUNT", account)
    save_local_value(
        "BANKING_FOUNDRY_ENDPOINT",
        f"https://{account}.services.ai.azure.com/api/projects/{config['project_name']}",
    )
    return refresh_project_state(load_config("banking"))


def confirm_voice_version(config, locale="en-GB"):
    versions = Foundry(config).request(
        "GET", f"/agents/{config['voice_agent_name']}/versions?api-version=v1",
        headers={"Foundry-Features": "VoiceAgents=V1Preview"},
    ).get("data", [])
    if not versions:
        raise RuntimeError("Deployed voice agent could not be read back.")
    latest = max(versions, key=lambda version: int(version["version"]))
    definition = latest.get("definition", {})
    expected = voice_definition(locale)

    def matches(actual, desired):
        if isinstance(desired, dict):
            return isinstance(actual, dict) and all(
                key in actual and matches(actual[key], value) for key, value in desired.items())
        return actual == desired

    echo = definition.get("audio", {}).get("input", {}).get("echo_cancellation", {})
    if (not matches(definition, expected) or echo.get("reference_source", "server") != "server"
            or echo.get("channels", 1) != 1):
        raise RuntimeError("Readback did not confirm the model, native voice, audio, greeting, tools and privacy settings.")
    suffix = {"en-GB": "", "az-AZ": "_AZ", "ru-RU": "_RU"}[locale]
    save_local_value(f"BANKING_VOICE{suffix}_AGENT_VERSION", str(latest["version"]))
    return str(latest["version"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--apply", action="store_true", help="Apply the previewed infrastructure.")
    operation.add_argument("--deploy-voice", action="store_true",
                           help="Deploy the managed voice service to a ready project.")
    parser.add_argument("--locale", choices=VOICE_PROFILES, default="en-GB",
                        help="Native voice profile; non-English profiles add a locale suffix to the agent name.")
    options = parser.parse_args(argv)
    config = load_config("banking")
    if options.locale != "en-GB" and not options.deploy_voice:
        raise ValueError("--locale requires --deploy-voice; infrastructure remains shared.")
    if not config.get("voice_agent_name"):
        raise ValueError("Missing banking setting: voice_agent_name")
    config = {**config, "voice_agent_name": config["voice_agent_name"] + VOICE_PROFILES[options.locale][0]}
    manifest = render_manifest(config, options.locale)
    tenant = azure("account", "show", "--subscription", config["subscription_id"],
                   "--query", "tenantId", "-o", "tsv").strip()
    if tenant.lower() != config["tenant_id"].lower():
        raise RuntimeError("Azure CLI tenant does not match local configuration.")
    target = json.dumps([config[key] for key in ("subscription_id", "resource_group", "project_name")])
    target_id = hashlib.sha256(target.encode()).hexdigest()[:16]
    project = ROOT / ".azure" / "foundry-project" / target_id
    project.mkdir(parents=True, exist_ok=True)
    (project / "azure.yaml").write_text(manifest, encoding="utf-8")
    azd(project, "config", "set", "auth.useAzCliAuth", "true")
    if not (project / ".azure" / "banking" / ".env").exists():
        azd(project, "env", "new", "banking", "--subscription", config["subscription_id"],
            "--location", config["region"], "--no-prompt")
    for key, value in {
        "AZURE_SUBSCRIPTION_ID": config["subscription_id"],
        "AZURE_LOCATION": config["region"],
        "AZURE_RESOURCE_GROUP": config["resource_group"],
        "AZURE_AI_PROJECT_NAME": config["project_name"],
        "ENABLE_HOSTED_AGENTS": "true",
        "ENABLE_CAPABILITY_HOST": "false",
    }.items():
        azd(project, "env", "set", key, value)
    if options.deploy_voice:
        if refresh_project_state(config) != "Succeeded":
            raise RuntimeError("The Foundry project must be ready before voice deployment.")
        azd(project, "deploy", "banking-voice-concierge", "--environment", "banking", "--no-prompt")
        confirm_voice_version(config, options.locale)
        print("Native voice definition confirmed. Run VerifyVoice to check actual audio.")
        return 0
    arguments = ["provision", "--environment", "banking", "--no-prompt"]
    if not options.apply:
        azd(project, *arguments, "--preview")
        return 0
    save_local_value("BANKING_PROJECT_STATE", "Creating")
    save_local_value("BANKING_VOICE_AGENT_VERSION", "")
    try:
        azd(project, *arguments)
    except (subprocess.SubprocessError, OSError):
        try:
            print(f"Live project state: {refresh_cloud_config(config)}")
        except (RuntimeError, ValueError, OSError) as exc:
            save_local_value("BANKING_PROJECT_STATE", "Unknown")
            print(f"Project status readback failed: {exc}")
        raise
    state = refresh_cloud_config(config)
    if state != "Succeeded":
        raise RuntimeError(f"Project is not ready: {state}")
    Foundry(load_config("banking")).request("GET", "/agents?api-version=v1")
    print("Foundry infrastructure is ready. Agent deployment and voice verification are separate gates.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FoundryError, RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"Fresh Foundry deployment failed: {exc}")
        raise SystemExit(1)
