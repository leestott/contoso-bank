"""Invoke real hosted workers before executing server-governed synthetic actions."""

import hashlib
import hmac
import re
import uuid

from commerce import DomainError
from foundry import FoundryError

ROUTES = {
    "freeze_card": "card", "unfreeze_card": "card", "transactions": "card",
    "replace_card": "card", "virtual_card": "card",
    "statements": "lending", "prepare_memo": "lending", "loan_status": "lending",
    "offers": "payments", "create_mandate": "payments", "pay": "payments",
    "one_off_request": "payments",
}
COMMON = {"profile", "verify_start", "verify_complete", "handoff", "knowledge", "survey", "set_locale"}


ORCHESTRATOR = "/agents/banking-orchestrator/endpoint"


class HostedBanking:
    """Broker between the authenticated bank API and the hosted workers.

    In governed mode the broker sends only an opaque single-use grant; the deterministic bank
    action runs when the hosted orchestrator presents that grant to the governed gateway through
    the Foundry Toolbox MCP. Hosted sessions are delegated to a server-derived per-customer
    identity (x-ms-user-identity) so each customer gets an isolated persistent $HOME.
    """

    def __init__(self, bank, client, grants=None, governed=False, isolation_secret=None, version=None):
        self.bank, self.client, self.grants = bank, client, grants
        self.governed, self.version = governed, version
        self.isolation = (hmac.new(isolation_secret.encode(), b"hosted-isolation", hashlib.sha256).digest()
                          if isolation_secret else None)
        if governed and grants is None:
            raise ValueError("Governed hosted banking requires a grant store.")
        if self.isolation:
            with bank.db() as db:
                db.execute("CREATE TABLE IF NOT EXISTS hosted_agent_sessions(scope TEXT NOT NULL,"
                           " version TEXT NOT NULL, agent_session_id TEXT NOT NULL, created_at TEXT NOT NULL,"
                           " PRIMARY KEY(scope, version))")

    def identity(self, owner, customer):
        # Derived only from the authenticated presenter and the server-held session customer.
        digest = hmac.new(self.isolation, f"{owner}\n{customer}".encode(), hashlib.sha256).hexdigest()
        return "cust-" + digest[:48]

    def owner_customer(self, session):
        with self.bank.db() as db:
            row = db.execute("SELECT o.owner, s.customer FROM bank_owners o JOIN bank_sessions s"
                             " ON s.id=o.session WHERE o.session=?", (session,)).fetchone()
        if not row:
            raise DomainError("This banking session has no authenticated owner.", 403)
        return row["owner"], row["customer"]

    def hosted_session(self, session, renew=False):
        if not self.isolation:
            return None, {}
        user = self.identity(*self.owner_customer(session))
        version = str(self.version or "")
        with self.bank.db() as db:
            row = None if renew else db.execute(
                "SELECT agent_session_id FROM hosted_agent_sessions WHERE scope=? AND version=?",
                (user, version)).fetchone()
        headers = {"x-ms-user-identity": user}
        if row:
            return row["agent_session_id"], headers
        body = {"version_indicator": {"type": "version_ref", "agent_version": version}} if version else {}
        created = self.client.request("POST", ORCHESTRATOR + "/sessions?api-version=v1", body, headers)
        identifier = created.get("agent_session_id")
        if not isinstance(identifier, str) or not identifier.isascii() or not identifier.isalnum():
            raise FoundryError("Hosted session was not created; no action was executed.")
        with self.bank.db() as db:
            db.execute("INSERT OR REPLACE INTO hosted_agent_sessions VALUES(?,?,?,datetime('now'))",
                       (user, version, identifier))
        return identifier, headers

    def invoke(self, session, body):
        for attempt in (0, 1):
            identifier, headers = self.hosted_session(session, renew=attempt == 1)
            query = "&agent_session_id=" + identifier if identifier else ""
            try:
                path = ORCHESTRATOR + "/protocols/invocations?api-version=v1" + query
                return self.client.request("POST", path, body, headers) if headers else \
                    self.client.request("POST", path, body)
            except FoundryError as exc:
                # An expired or deleted hosted session is replaced once; nothing ran on a 404.
                if not identifier or attempt or getattr(exc, "status", None) not in (404, 410):
                    raise

    def action(self, session, operation, payload=None):
        if not isinstance(operation, str) or operation not in COMMON | ROUTES.keys():
            raise DomainError("Unsupported banking tool operation.")
        if payload is not None and not isinstance(payload, dict):
            raise DomainError("Banking payload must be a JSON object.")
        state = self.bank.view(session)
        request_id = uuid.uuid4().hex
        role = ROUTES.get(operation, "orchestrator")
        # Credentials, OTPs, passcodes and mandate contents never go to hosted worker models.
        context = {key: state[key] for key in ("customer", "locale", "verified", "card")}
        context["memo_status"] = (state.get("memo") or {}).get("status")
        query = (payload or {}).get("query", (payload or {}).get("question")) if operation == "knowledge" else None
        if isinstance(query, str) and 0 < len(query.strip()) <= 150 and not re.search(r"\d{4}", query):
            context["query"] = query.strip()
        body = {"operation": operation, "context": context, "request_id": request_id}
        grant = None
        if self.governed:
            grant = self.grants.issue(session, operation, payload, role, request_id,
                                      audit_only=operation == "knowledge" and "query" in context)
            body["grant"] = grant
        try:
            result = self.invoke(session, body)
        except FoundryError:
            record = self.grants.collect(grant) if grant else None
            if record and record.get("refusal"):
                raise DomainError(*record["refusal"]) from None
            raise
        record = self.grants.collect(grant) if grant else None
        if record and record.get("refusal"):
            raise DomainError(*record["refusal"])
        if (result.get("role") != role or result.get("request_id") != request_id
                or result.get("operation") != operation or result.get("agent") != "banking-" + role
                or not isinstance(result.get("model_response_id"), str) or not result["model_response_id"]
                or (role != "orchestrator" and result.get("orchestrator") != "banking-orchestrator")):
            raise FoundryError("Hosted banking execution was not confirmed; no action was executed.")
        evidence = {key: result[key] for key in ("agent", "role", "request_id", "model_response_id")}
        if self.governed:
            receipt = result.get("gateway") or {}
            if (not record or not record["consumed"] or record.get("refusal") or record["result"] is None
                    or receipt.get("request_id") != request_id or receipt.get("executed") is not True):
                raise FoundryError("The governed banking gateway did not confirm execution; "
                                   "no success is reported.")
            value = dict(record["result"])
            evidence = {**record["evidence"], "gateway_receipt": receipt}
        else:
            value = self.bank.action(session, operation, payload, actor="hosted:banking-" + role, evidence=evidence)
        value["hosted_execution"] = evidence
        if isinstance(result.get("knowledge"), dict):
            value["knowledge"] = result["knowledge"]
        session_file = result.get("session_file")
        if isinstance(session_file, dict):
            value["hosted_session_file"] = {key: session_file.get(key)
                                            for key in ("path", "entries", "first_entry_at")}
        return value
