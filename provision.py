"""Create the saved recommendation agent after Azure project provisioning."""

import json
from foundry import Foundry, ROOT


def main():
    client = Foundry()
    definition = json.loads((ROOT / "agent.json").read_text())
    if not client.config["agent_name"] or not client.config["model_deployment"]:
        raise ValueError("Set COMMERCE_AGENT_NAME and COMMERCE_MODEL_DEPLOYMENT in your local .env.")
    definition["name"] = client.config["agent_name"]
    definition["definition"]["model"] = client.config["model_deployment"]
    # Do not silently overwrite an existing version or create duplicate agents.
    agents = client.request("GET", "/agents?api-version=v1")
    existing = next((a for a in agents["data"] if a["name"] == definition["name"]), None)
    if existing:
        version = client.request(
            "GET", f"/agents/{definition['name']}/versions/{client.config['agent_version']}?api-version=v1"
        )
        if version["definition"] != definition["definition"]:
            raise RuntimeError("The agent definition differs. Create a version explicitly in Foundry.")
        print(json.dumps({"name": existing["name"], "version": version["version"], "existing": True}))
        return
    result = client.request("POST", "/agents?api-version=v1", definition)
    print(json.dumps({"name": result["name"], "version": result["versions"]["latest"]["version"]}))


if __name__ == "__main__":
    main()
