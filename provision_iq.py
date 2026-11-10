"""Provision the trilingual Foundry IQ knowledge base and its read-only toolbox.

The knowledge base deliberately has no model: a Free-tier search service has no
managed identity for LLM calls, so retrieval is minimal/extractive and returns
cited policy passages. Answer wording stays with the governed banking flow.
"""

import json
import subprocess
import shutil
import sys
import urllib.error
import urllib.request

from banking import KNOWLEDGE
from foundry import Foundry
from settings import load_config, read_environment, save_local_value

API = "2026-05-01-preview"
INDEX, SOURCE, BASE = "bank-policy-index", "bank-policy-ks", "bank-policy-kb"
CONNECTION, TOOLBOX = "bank-policy-kb-mcp", "bank-policy-knowledge"
SEMANTIC = "policy-semantic"
LANGUAGE = {"en-GB": "en", "ru-RU": "ru", "az-AZ": "az"}


def token(resource, config):
    az = shutil.which("az")
    out = subprocess.run([az, "account", "get-access-token", "--subscription", config["subscription_id"],
                          "--resource", resource, "-o", "json"], capture_output=True, text=True, check=True)
    value = json.loads(out.stdout)
    if value.get("tenant") != config["tenant_id"]:
        raise RuntimeError("Unexpected token tenant.")
    return value["accessToken"]


def http(method, url, bearer, body=None):
    request = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + bearer, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:600]
        raise RuntimeError(f"{method} {url.split('?')[0]} returned HTTP {exc.code}: {detail}") from None


def documents():
    return [{"id": item["id"], "title": item["title"], "locale": item["locale"],
             "language": LANGUAGE[item["locale"]], "content": item["text"]} for item in KNOWLEDGE]


def main():
    config = load_config("banking")
    env = read_environment()
    endpoint = env.get("BANKING_SEARCH_ENDPOINT", "").rstrip("/")
    if not endpoint.startswith("https://") or not endpoint.endswith(".search.windows.net"):
        raise RuntimeError("Set BANKING_SEARCH_ENDPOINT in the ignored .env first.")
    search = token("https://search.azure.com", config)
    put = lambda path, body: http("PUT", f"{endpoint}/{path}?api-version={API}", search, body)
    put(f"indexes/{INDEX}", {
        "name": INDEX,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
            {"name": "title", "type": "Edm.String", "searchable": True},
            {"name": "locale", "type": "Edm.String", "filterable": True},
            {"name": "language", "type": "Edm.String", "filterable": True},
            {"name": "content", "type": "Edm.String", "searchable": True, "analyzer": "standard.lucene"},
        ],
        "semantic": {"defaultConfiguration": SEMANTIC, "configurations": [{
            "name": SEMANTIC, "prioritizedFields": {
                "titleField": {"fieldName": "title"},
                "prioritizedContentFields": [{"fieldName": "content"}]}}]},
    })
    http("POST", f"{endpoint}/indexes/{INDEX}/docs/index?api-version={API}", search,
         {"value": [{"@search.action": "mergeOrUpload", **doc} for doc in documents()]})
    put(f"knowledgesources/{SOURCE}", {
        "name": SOURCE, "kind": "searchIndex",
        "description": "Synthetic Contoso Bank policies in English, Russian and Azerbaijani.",
        "searchIndexParameters": {
            "searchIndexName": INDEX, "semanticConfigurationName": SEMANTIC,
            "sourceDataFields": [{"name": "id"}, {"name": "title"}, {"name": "locale"}, {"name": "content"}],
            "searchFields": [{"name": "content"}, {"name": "title"}]},
    })
    put(f"knowledgebases/{BASE}", {
        "name": BASE, "description": "Read-only trilingual synthetic bank policy knowledge.",
        "knowledgeSources": [{"name": SOURCE}],
        "retrievalReasoningEffort": {"kind": "minimal"}, "outputMode": "extractiveData",
    })
    mcp = f"{endpoint}/knowledgebases/{BASE}/mcp?api-version={API}"
    save_local_value("BANKING_SEARCH_KNOWLEDGE_BASE", BASE)
    print(f"Knowledge base ready with {len(KNOWLEDGE)} trilingual policy documents.")
    if "--toolbox" not in sys.argv:
        return
    arm = token("https://management.azure.com", config)
    project_id = (f"/subscriptions/{config['subscription_id']}/resourceGroups/{config['resource_group']}"
                  f"/providers/Microsoft.CognitiveServices/accounts/{config['account_name']}"
                  f"/projects/{config['project_name']}")
    http("PUT", f"https://management.azure.com{project_id}/connections/{CONNECTION}?api-version=2025-10-01-preview",
         arm, {"properties": {"category": "RemoteTool",          "authType": "ProjectManagedIdentity", "target": mcp,
                                       "audience": "https://search.azure.com/",  "isSharedToAll": True,
                              "metadata": {"ApiType": "Azure"}}})
    foundry = Foundry(config)
    version = foundry.request("POST", f"/toolboxes/{TOOLBOX}/versions?api-version=v1", {
        "description": "Read-only Foundry IQ bank policy retrieval; no banking actions.",
        "tools": [{"type": "mcp", "server_label": "knowledge_base", "server_url": mcp,
                   "project_connection_id": CONNECTION, "require_approval": "never",
                   "allowed_tools": ["knowledge_base_retrieve"]}],
    })
    save_local_value("BANKING_KNOWLEDGE_TOOLBOX", TOOLBOX)
    save_local_value("BANKING_KNOWLEDGE_TOOLBOX_VERSION", str(version.get("version", "")))
    print(f"Toolbox {TOOLBOX} version {version.get('version')} created.")


if __name__ == "__main__":
    main()
