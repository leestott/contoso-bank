"""Governed banking MCP gateway reached only through the Foundry Toolbox connection.

The broker issues a single-use grant that binds a server-verified session, operation and
payload. Hosted workers only ever see the opaque grant; the gateway re-validates the grant
and executes the deterministic banking action server-side.
"""

import json
import secrets
import threading
import time

from commerce import DomainError

TOOL = "execute_banking_operation"
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")
ROLES = {"orchestrator", "card", "lending", "payments"}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["grant", "agent", "role", "request_id", "model_response_id"],
    "properties": {
        "grant": {"type": "string", "description": "Opaque single-use grant issued by the banking broker."},
        "agent": {"type": "string"}, "role": {"type": "string", "enum": sorted(ROLES)},
        "request_id": {"type": "string"}, "model_response_id": {"type": "string"},
    },
}


class Grants:
    def __init__(self, ttl=120, clock=time.monotonic):
        self.ttl, self.clock, self.items, self.lock = ttl, clock, {}, threading.Lock()

    def issue(self, session, operation, payload, role, request_id, audit_only=False):
        grant = secrets.token_urlsafe(32)
        with self.lock:
            now = self.clock()
            self.items = {key: value for key, value in self.items.items() if value["expires"] > now}
            self.items[grant] = {"session": session, "operation": operation, "payload": payload,
                                 "role": role, "request_id": request_id, "audit_only": audit_only,
                                 "expires": now + self.ttl, "consumed": False, "result": None,
                                 "evidence": None}
        return grant

    def claim(self, grant, arguments):
        with self.lock:
            record = self.items.get(grant) if isinstance(grant, str) else None
            if (not record or record["consumed"] or record["expires"] <= self.clock()
                    or arguments.get("request_id") != record["request_id"]
                    or arguments.get("role") != record["role"]
                    or arguments.get("agent") != "banking-" + record["role"]
                    or not isinstance(arguments.get("model_response_id"), str)
                    or not arguments["model_response_id"]):
                raise DomainError("Banking grant is invalid, expired, already used or mismatched.", 403)
            record["consumed"] = True
            return record

    def collect(self, grant):
        with self.lock:
            return self.items.pop(grant, None)


def rpc(identifier, result=None, error=None):
    value = {"jsonrpc": "2.0", "id": identifier}
    value.update({"error": error} if error else {"result": result})
    return value


def handle(bank, grants, body):
    """Return (status, JSON-RPC response or None) for one MCP Streamable HTTP POST."""
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or not isinstance(body.get("method"), str):
        return 400, rpc(None, error={"code": -32600, "message": "Invalid JSON-RPC request."})
    method, identifier = body["method"], body.get("id")
    if identifier is None:
        return 202, None
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        return 200, rpc(identifier, {
            "protocolVersion": requested if requested in PROTOCOLS else PROTOCOLS[-1],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "contoso-bank-governed-gateway", "version": "1"}})
    if method == "ping":
        return 200, rpc(identifier, {})
    if method == "tools/list":
        return 200, rpc(identifier, {"tools": [{
            "name": TOOL, "inputSchema": SCHEMA,
            "description": ("Execute one broker-authorized synthetic banking operation. Requires an "
                            "opaque single-use grant; identity, OTP, mandate and customer checks are "
                            "enforced by the bank server, never by the caller.")}]})
    if method != "tools/call":
        return 200, rpc(identifier, error={"code": -32601, "message": "Method not found."})
    arguments = params.get("arguments")
    if params.get("name") != TOOL or not isinstance(arguments, dict) or set(arguments) - set(SCHEMA["properties"]):
        return 200, rpc(identifier, error={"code": -32602, "message": "Unknown tool or arguments."})
    try:
        record = grants.claim(arguments.get("grant"), arguments)
    except DomainError as exc:
        return 200, rpc(identifier, {"isError": True, "content": [{"type": "text", "text": str(exc)}]})
    evidence = {"agent": arguments["agent"], "role": record["role"], "request_id": record["request_id"],
                "model_response_id": arguments["model_response_id"],
                "gateway": "Foundry Toolbox MCP -> governed banking gateway"}
    actor = "hosted:banking-" + record["role"]
    try:
        if record["audit_only"]:
            result = bank.record(record["session"], record["operation"], actor,
                                 {"read_only": True, "grounding": "Foundry IQ", "hosted_execution": evidence})
        else:
            result = bank.action(record["session"], record["operation"], record["payload"],
                                 actor=actor, evidence=evidence)
    except DomainError as exc:
        # The bank's deterministic refusal is returned to the broker; only a generic receipt leaves.
        record.update(refusal=(str(exc), exc.status), evidence=evidence)
        receipt = {"executed": False, "operation": record["operation"], "request_id": record["request_id"],
                   "outcome": "refused"}
        return 200, rpc(identifier, {"content": [{"type": "text", "text": "The bank refused the operation."}],
                                     "structuredContent": receipt, "isError": True})
    record.update(result=result, evidence=evidence)
    receipt = {"executed": True, "operation": record["operation"], "request_id": record["request_id"],
               "outcome": "refused" if isinstance(result, dict) and "error" in result else "completed"}
    return 200, rpc(identifier, {"content": [{"type": "text", "text": json.dumps(receipt)}],
                                 "structuredContent": receipt, "isError": False})
