"""Provision/read back a native voice agent in the locally configured project."""

import json
import urllib.error

from foundry import Foundry, FoundryError, ROOT
from settings import load_config, save_local_value

FEATURES = {"Foundry-Features": "VoiceAgents=V1Preview"}


def main():
    config = load_config("banking")
    if config["project_state"] != "Succeeded":
        raise FoundryError("Confirm banking project readiness before provisioning voice.")
    client = Foundry(config)
    desired = json.loads((ROOT / "voice-agent.json").read_text())
    name = config["voice_agent_name"]
    if not name:
        raise ValueError("Set BANKING_VOICE_AGENT_NAME in your local .env.")
    desired["name"] = name
    try:
        existing = client.request("GET", f"/agents/{name}/versions?api-version=v1",
                                  headers=FEATURES)
    except (FoundryError, ValueError) as exc:
        if not isinstance(exc.__cause__, urllib.error.HTTPError) or exc.__cause__.code != 404:
            raise
        existing = {"data": []}
    versions = existing.get("data", [])
    matching = [v for v in versions if v.get("definition") == desired["definition"]]
    if matching:
        result = matching[0]
    elif versions:
        raise FoundryError("Existing voice definition differs. Review before creating a new version.")
    else:
        created = client.request("POST", "/agents?api-version=v1", desired, headers=FEATURES)
        result = created.get("versions", {}).get("latest", created)
    version = str(result["version"])
    saved = client.request("GET", f"/agents/{name}/versions/{version}?api-version=v1",
                           headers=FEATURES)
    if saved.get("definition", {}).get("kind") != "voice":
        raise FoundryError("Read-back did not confirm a native voice agent.")
    save_local_value("BANKING_VOICE_AGENT_VERSION", version)
    print(json.dumps({"name": name, "version": version, "kind": "voice",
                      "project": config["project_endpoint"]}))


if __name__ == "__main__":
    try:
        main()
    except FoundryError as exc:
        print(str(exc))
        if isinstance(exc.__cause__, urllib.error.HTTPError):
            print(exc.__cause__.read().decode()[:2000])
        raise SystemExit(1)
