"""Invoke all four real hosted workers, including specialist routing and inference."""

import uuid

from foundry import Foundry
from settings import load_config


def verify():
    client = Foundry(load_config("banking"))
    for role, operation in (("orchestrator", "profile"), ("card", "transactions"),
                            ("lending", "statements"), ("payments", "offers")):
        body = {"operation": operation, "request_id": uuid.uuid4().hex,
                "context": {"customer": "leyla", "locale": "en-GB", "verified": False,
                            "card": "active", "memo_status": None}}
        result = client.request("POST",
            "/agents/banking-orchestrator/endpoint/protocols/invocations?api-version=v1", body)
        if (result.get("role") != role or result.get("request_id") != body["request_id"]
                or not result.get("model_response_id")):
            raise RuntimeError("The live hosted invocation was not confirmed: " + role)
        print("Live hosted invocation confirmed:", role, result["model_response_id"], flush=True)


if __name__ == "__main__":
    verify()
