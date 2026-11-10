"""Synthetic banking journeys. Policy and verification are enforced outside prompts."""

import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from commerce import DomainError, integer
from settings import survey_url

SUPPORTED_LOCALES = {"az-AZ", "ru-RU", "en-GB"}

CUSTOMERS = {
    "leyla": {"name": "Leyla Mammadova", "locale": "az-AZ", "dob": "1992-04-17",
              "phone": "+994 ** *** 0134", "address": "12 Demo Street, Baku (synthetic)",
              "balance": 245000, "products": ["Current account", "Debit card", "Savings", "Mobile banking"]},
    "dmitri": {"name": "Dmitri Sokolov", "locale": "ru-RU", "dob": "1974-02-08",
               "phone": "+994 ** *** 0152", "address": "28 Demo Bakery Road, Ganja (synthetic)",
               "balance": 6800000, "products": ["Business account", "Payroll: 28 staff", "Merchant acquiring: three sites"]},
    "james": {"name": "James Carter", "locale": "en-GB", "dob": "1985-06-21",
              "phone": "+994 ** *** 0141", "address": "41 Demo Avenue, Baku (synthetic)",
              "balance": 890000, "products": ["Premium AZN/GBP account", "Credit card", "Travel insurance"]},
}
OFFERS = {
    "renewal-existing": {"id": "renewal-existing", "label": "Existing-cover travel renewal",
                         "merchant": "Contoso Insurance", "amount": 24000,
                         "cover": "Worldwide annual, medical and baggage; matches existing cover"},
    "renewal-plus": {"id": "renewal-plus", "label": "Travel Plus renewal",
                     "merchant": "Contoso Insurance", "amount": 29000,
                     "cover": "Adds winter sports; not the existing-cover match"},
    "electricity": {"id": "electricity", "label": "Electricity bill",
                    "merchant": "Baku Demo Utilities", "amount": 8500, "cover": "Synthetic household bill"},
    "flight-upgrade": {"id": "flight-upgrade", "label": "Flight upgrade",
                       "merchant": "Demo Air", "amount": 65000, "cover": "Not an approved merchant"},
}
MERCHANTS = ["Contoso Insurance", "Baku Demo Utilities"]
KNOWLEDGE = [
    {"id": "CARD-01", "title": "Synthetic card policy", "locale": "en-GB",
     "text": "Verify date of birth and simulated OTP before account actions. Freeze lost cards, replace only to the registered address, and offer an immediate virtual card. Replacement fee: 0 AZN."},
    {"id": "CARD-01-AZ", "title": "Sintetik kart qaydaları", "locale": "az-AZ",
     "text": "Hesab əməliyyatından əvvəl doğum tarixini və birdəfəlik kodu yoxlayın. İtmiş kartı dondurun. Yeni kartı yalnız qeydiyyat ünvanına göndərin. Virtual kart təklif edin. Əvəzləmə haqqı: 0 AZN."},
    {"id": "SME-01", "title": "Synthetic SME policy", "locale": "en-GB",
     "text": "Illustrative only: maximum 100000 AZN, 24 months, 12% annual nominal interest, minimum debt-service coverage 1.25. Assess 12 months of statements. Rashad Aliyev alone decides an indicative offer. No binding lending or credit decision by an agent."},
    {"id": "SME-01-RU", "title": "Синтетическая политика МСП", "locale": "ru-RU",
     "text": "Только демонстрация: до 100000 AZN, 24 месяца, 12% годовых, покрытие долга не ниже 1,25. Анализ за 12 месяцев. Только Рашад Алиев принимает решение по предварительному предложению."},
    {"id": "PAY-01", "title": "Synthetic payment mandate", "locale": "en-GB",
     "text": "Mandates require passcode confirmation, expire in 30 days, permit only Contoso Insurance and Baku Demo Utilities, and allow at most 300 AZN per payment and 500 AZN per UTC calendar month. A one-off request never changes the mandate."},
    {"id": "PAY-01-AZ", "title": "Sintetik ödəniş mandatı", "locale": "az-AZ",
     "text": "Mandat təsdiq kodu tələb edir, 30 gün qüvvədədir. Yalnız Contoso Insurance və Baku Demo Utilities üçün hər ödəniş 300 AZN, ayda 500 AZN həddi tətbiq olunur."},
    {"id": "PAY-01-RU", "title": "Синтетический платёжный мандат", "locale": "ru-RU",
     "text": "Подтверждение кодом, срок 30 дней. Только Contoso Insurance и Baku Demo Utilities. Лимит 300 AZN за операцию, 500 AZN в календарный месяц UTC. Разовый запрос не изменяет мандат."},
]


def utcnow():
    return datetime.now(timezone.utc)


class Bank:
    def __init__(self, path, clock=utcnow):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS bank_sessions (
                    id TEXT PRIMARY KEY, customer TEXT NOT NULL, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bank_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                    session TEXT NOT NULL, actor TEXT NOT NULL, operation TEXT NOT NULL,
                    detail TEXT NOT NULL, previous TEXT NOT NULL, hash TEXT NOT NULL);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, db, session, actor, operation, detail):
        previous = db.execute("SELECT hash FROM bank_events ORDER BY seq DESC LIMIT 1").fetchone()
        previous = previous["hash"] if previous else "0" * 64
        at = self.clock().isoformat()
        detail = json.dumps(detail, sort_keys=True, ensure_ascii=False)
        raw = json.dumps([at, session, actor, operation, detail, previous], ensure_ascii=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        db.execute("INSERT INTO bank_events(at,session,actor,operation,detail,previous,hash) VALUES(?,?,?,?,?,?,?)",
                   (at, session, actor, operation, detail, previous, digest))

    def start(self, customer, locale=None):
        if not isinstance(customer, str) or customer not in CUSTOMERS:
            raise DomainError("Select one of the three synthetic customers.")
        if locale is None:
            locale = CUSTOMERS[customer]["locale"]
        if not isinstance(locale, str) or locale not in SUPPORTED_LOCALES:
            raise DomainError("Supported locales: az-AZ, ru-RU, en-GB.")
        session = secrets.token_hex(24)
        state = {"verified": False, "otp_hash": None, "otp_expires": None, "attempts": 0,
                 "card": "active", "replacement": None, "virtual_card": False,
                 "messages": [], "handoff": None, "mandate": None, "payments": [],
                 "one_off_requests": [], "memo": None, "transcript": [], "locale": locale}
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO bank_sessions VALUES(?,?,?)",
                       (session, customer, json.dumps(state)))
            self.event(db, session, "demo-operator", "session_started",
                       {"customer": customer, "synthetic": True})
        return {"session": session, "customer": customer, "name": CUSTOMERS[customer]["name"],
                "locale": state["locale"]}

    def load(self, db, session):
        if not isinstance(session, str):
            raise DomainError("Start a synthetic customer session.", 401)
        row = db.execute("SELECT * FROM bank_sessions WHERE id=?", (session,)).fetchone()
        if not row:
            raise DomainError("Unknown customer session.", 401)
        return row["customer"], json.loads(row["state"])

    def save(self, db, session, state):
        db.execute("UPDATE bank_sessions SET state=? WHERE id=?", (json.dumps(state), session))

    def action(self, session, operation, payload=None, actor="local-demo-tool", evidence=None):
        payload = payload if payload is not None else {}
        if not isinstance(payload, dict) or not isinstance(operation, str):
            raise DomainError("Operation and JSON object payload are required.")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            customer, state = self.load(db, session)
            # Callers cannot select a different customer or supply authoritative prices/roles.
            if any(k in payload for k in ("customer", "customer_id", "session", "actor", "balance", "merchant", "price")):
                self.event(db, session, actor, "cross_customer_or_policy_override_refused",
                           {"operation": operation, **({"hosted_execution": evidence} if evidence else {})})
                result = {"error": "Customer scope and financial values are server-owned.", "flagged": True}
            else:
                try:
                    result = self.execute(customer, state, operation, payload)
                except DomainError as exc:
                    result = {"error": str(exc)}
                self.event(db, session, actor, operation,
                           {"result": result, "mandate_reference": (state.get("mandate") or {}).get("id"),
                            **({"hosted_execution": evidence} if evidence else {})})
            self.save(db, session, state)
            return result

    def record(self, session, operation, actor, detail):
        """Audit a read-only hosted operation without changing customer state."""
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            self.load(db, session)
            self.event(db, session, actor, operation, detail)
        return {"recorded": True, "read_only": True}

    def execute(self, customer, state, operation, p):
        fixture = CUSTOMERS[customer]
        if operation == "profile":
            return {"name": fixture["name"], "locale": state["locale"], "synthetic": True,
                    "verified": state["verified"], "human_available": "handoff queue (demo)"}
        if operation == "verify_start":
            if state["attempts"] >= 5:
                raise DomainError("Verification locked. Request a human.")
            if p.get("date_of_birth") != fixture["dob"]:
                state["attempts"] += 1
                raise DomainError("Date of birth did not match the synthetic fixture.")
            otp = f"{secrets.randbelow(1000000):06d}"
            state["otp_hash"] = hashlib.sha256(otp.encode()).hexdigest()
            state["otp_expires"] = (self.clock() + timedelta(minutes=3)).isoformat()
            state["messages"].append({"kind": "simulated-otp", "text": otp, "to": fixture["phone"]})
            return {"sent": True, "channel": "simulated SMS in banking app", "to": fixture["phone"]}
        if operation == "verify_complete":
            code = p.get("otp")
            if state["attempts"] >= 5:
                raise DomainError("Verification locked. Request a human.")
            if (not state["otp_hash"] or not isinstance(code, str)
                    or self.clock() >= datetime.fromisoformat(state["otp_expires"])
                    or not hmac.compare_digest(hashlib.sha256(code.encode()).hexdigest(), state["otp_hash"])):
                state["attempts"] += 1
                raise DomainError("Invalid or expired simulated OTP.")
            state.update(verified=True, otp_hash=None, otp_expires=None)
            return {"verified": True}
        if operation == "handoff":
            state["handoff"] = {"status": "queued", "team": "Aynur Hasanova / trilingual contact centre",
                                "reason": str(p.get("reason", "Caller requested a human"))[:500]}
            return state["handoff"]
        if operation == "knowledge":
            locale = p.get("locale", state["locale"])
            return {"sources": [k for k in KNOWLEDGE if k["locale"] in (locale, "en-GB")],
                    "grounding": "local synthetic policy corpus; not Foundry IQ"}
        if operation == "survey":
            url = survey_url()
            if not url:
                raise DomainError("No demo survey is configured.")
            return {"url": url, "participation_target": "70%; not measured by this demo"}
        if operation == "set_locale":
            if not isinstance(p.get("locale"), str) or p["locale"] not in SUPPORTED_LOCALES:
                raise DomainError("Supported locales: az-AZ, ru-RU, en-GB.")
            state["locale"] = p["locale"]
            return {"locale": state["locale"]}
        if not state["verified"]:
            raise DomainError("Verify date of birth and simulated OTP before account actions.")
        if operation in {"freeze_card", "unfreeze_card", "transactions", "replace_card", "virtual_card"}:
            if customer != "leyla":
                raise DomainError("The lost-card scene is scoped to Leyla.")
            if operation == "freeze_card":
                state["card"] = "frozen"
                return {"card": state["card"]}
            if operation == "unfreeze_card":
                if state["replacement"]:
                    raise DomainError("A replaced card cannot be reactivated.")
                state["card"] = "active"
                return {"card": state["card"]}
            if operation == "transactions":
                return {"transactions": [
                    {"merchant": "Demo Baku Market", "amount": -4250, "currency": "AZN"},
                    {"merchant": "Demo Metro", "amount": -100, "currency": "AZN"},
                    {"merchant": "Demo Cafe", "amount": -850, "currency": "AZN"}]}
            if state["card"] != "frozen":
                raise DomainError("Freeze the lost card first.")
            if operation == "replace_card":
                state["replacement"] = fixture["address"]
                state["messages"].append({"kind": "summary", "text":
                    "Lost card frozen. Replacement to registered address. Virtual card available. All data synthetic."})
                return {"replacement_to": fixture["address"], "fee": 0, "currency": "AZN"}
            state["virtual_card"] = True
            return {"virtual_card": "ready", "wallet": "simulated; no actual wallet provisioning"}
        if operation in {"statements", "prepare_memo", "loan_status"}:
            if customer != "dmitri":
                raise DomainError("The SME scene is scoped to Dmitri.")
            statements = [{"month": f"2025-{m:02d}", "income": 6000000 + (m % 4) * 200000,
                           "expenses": 4500000 + (m % 3) * 100000} for m in range(1, 13)]
            if operation == "statements":
                return {"currency": "AZN", "money_unit": "qapik", "statements": statements,
                        "sites": 3, "staff": 28}
            if operation == "loan_status":
                return {"memo": state["memo"]}
            if state["memo"]:
                return {"memo": state["memo"], "reused": True}
            amount = integer(p.get("amount"), "Loan amount (whole AZN)", 1000, 100000)
            monthly_cash = sum(s["income"] - s["expenses"] for s in statements) // 12
            rate, months = 0.12 / 12, 24
            payment = round(amount * 100 * rate / (1 - (1 + rate) ** -months))
            coverage = round(monthly_cash / payment, 3)
            state["memo"] = {
                "id": "memo-" + secrets.token_hex(6), "status": "awaiting_human", "reviewer": "Rashad Aliyev",
                "amount_azn": amount, "term_months": months, "annual_nominal_rate": "12%",
                "monthly_payment_qapik": payment, "monthly_cash_flow_qapik": monthly_cash,
                "debt_service_coverage": coverage, "policy_threshold": 1.25,
                "policy_calculation": "within illustrative threshold" if coverage >= 1.25 else "below illustrative threshold",
                "sources": ["SME-01", "synthetic-statements-2025-01:2025-12"],
                "pages": [
                    f"PAGE 1 OF 2 — SYNTHETIC INDICATIVE CREDIT MEMO\nDmitri Sokolov: three bakery sites; 28 staff.\n"
                    f"Working capital for ovens: {amount} AZN over {months} months at 12% nominal annual interest.\n"
                    "Evidence: twelve synthetic monthly statements (2025-01 to 2025-12). SME-01 policy.\n"
                    f"Average net monthly cash flow: {monthly_cash / 100:.2f} AZN.\n"
                    "Statement amounts and thresholds are illustrative, not underwriting advice.",
                    f"PAGE 2 OF 2 — CALCULATIONS AND HUMAN REVIEW\n"
                    f"r = 0.12/12; n = 24; instalment = principal*r/(1-(1+r)^(-n)).\n"
                    f"Monthly payment: {payment / 100:.2f} AZN; debt-service coverage: {coverage}.\n"
                    "Illustrative minimum coverage: 1.25; maximum principal: 100000 AZN (SME-01).\n"
                    "Risks: seasonality, incomplete liabilities, unverified supplier quote; all require human review.\n"
                    "Decision reserved exclusively for Rashad Aliyev. No binding credit offer."],
            }
            return {"memo": state["memo"]}
        if customer != "james":
            raise DomainError("This operation is unavailable in the current customer scene.")
        if operation == "offers":
            return {"currency": "AZN", "money_unit": "qapik", "offers": list(OFFERS.values())}
        if operation == "create_mandate":
            if state["mandate"]:
                return {"mandate": state["mandate"], "reused": True}
            if p.get("passcode") != "246810" or p.get("confirmed") is not True:
                raise DomainError("Confirm the displayed mandate and synthetic passcode.")
            now = self.clock()
            state["mandate"] = {"id": "mandate-" + secrets.token_hex(8), "customer": customer,
                                "per_transaction": 30000, "monthly": 50000, "merchants": MERCHANTS,
                                "created": now.isoformat(), "expires": (now + timedelta(days=30)).isoformat(),
                                "currency": "AZN", "money_unit": "qapik"}
            return {"mandate": state["mandate"]}
        if operation not in {"pay", "one_off_request"}:
            raise DomainError("Unknown banking operation.")
        offer_id = p.get("offer_id")
        offer = OFFERS.get(offer_id) if isinstance(offer_id, str) else None
        if not offer:
            raise DomainError("Choose a server-owned offer.")
        mandate = state["mandate"]
        if not mandate or p.get("mandate_id") != mandate["id"]:
            raise DomainError("A matching customer mandate reference is required.")
        if operation == "one_off_request":
            request = {"id": secrets.token_hex(8), "offer": offer["id"], "status": "awaiting_human",
                       "mandate_reference": mandate["id"], "amount": offer["amount"],
                       "does_not_override_mandate": True}
            state["one_off_requests"].append(request)
            return {"request": request}
        now = self.clock()
        spent = sum(payment["amount"] for payment in state["payments"]
                    if payment["at"][:7] == now.isoformat()[:7])
        reasons = []
        if now >= datetime.fromisoformat(mandate["expires"]):
            reasons.append("mandate_expired")
        if offer["amount"] > mandate["per_transaction"]:
            reasons.append("per_transaction_limit")
        if offer["merchant"] not in mandate["merchants"]:
            reasons.append("unapproved_merchant")
        if spent + offer["amount"] > mandate["monthly"]:
            reasons.append("monthly_limit")
        if reasons:
            return {"blocked": True, "reasons": reasons, "mandate_reference": mandate["id"],
                    "one_off_available": True, "charged": False}
        if any(payment["offer"] == offer["id"] for payment in state["payments"]):
            raise DomainError("This synthetic bill has already been paid.")
        if offer["id"].startswith("renewal") and any(
                payment["offer"].startswith("renewal") for payment in state["payments"]):
            raise DomainError("The insurance renewal has already been paid.")
        payment = {"id": secrets.token_hex(8), "offer": offer["id"], "amount": offer["amount"],
                   "merchant": offer["merchant"], "mandate_reference": mandate["id"],
                   "at": now.isoformat(), "simulated": True}
        state["payments"].append(payment)
        return {"payment": payment, "monthly_remaining": mandate["monthly"] - spent - offer["amount"]}

    def view(self, session):
        with self.db() as db:
            customer, state = self.load(db, session)
        state.pop("otp_hash")
        return {"customer": customer, "name": CUSTOMERS[customer]["name"], **state}

    def transcript(self, session, speaker, text):
        if speaker not in {"caller", "agent", "system"} or not isinstance(text, str) or len(text) > 4000:
            raise DomainError("Invalid transcript entry.")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            _, state = self.load(db, session)
            entry = {"id": secrets.token_hex(6), "at": self.clock().isoformat(), "speaker": speaker,
                     "locale": state["locale"], "text": text,
                     "english": text if state["locale"] == "en-GB" else None,
                     "translation": "original" if state["locale"] == "en-GB" else "pending"}
            state["transcript"].append(entry)
            state["transcript"] = state["transcript"][-250:]
            self.save(db, session, state)
        return dict(entry)

    def translate(self, session, entry_id, english, source):
        if (not isinstance(entry_id, str) or not isinstance(source, str) or len(source) > 200
                or (english is not None and (not isinstance(english, str) or len(english) > 4000))):
            raise DomainError("Invalid transcript translation.")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            _, state = self.load(db, session)
            for entry in state["transcript"]:
                if entry.get("id") == entry_id and entry.get("translation") == "pending":
                    entry["english"] = english
                    entry["translation"] = source if english is not None else "unavailable"
                    self.save(db, session, state)
                    return True
        return False

    def decide(self, session, decision, acknowledged, actor="human-demo-approver"):
        if not isinstance(decision, str) or decision not in {"indicative_offer", "declined"} or acknowledged is not True:
            raise DomainError("A human must acknowledge and choose the indicative decision.")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            customer, state = self.load(db, session)
            if customer != "dmitri" or not state["memo"] or state["memo"]["status"] != "awaiting_human":
                raise DomainError("No SME memo awaits a decision.")
            state["memo"]["status"] = decision
            state["memo"]["decided_by"] = ("Rashad Aliyev (simulated local operator role)"
                                         if actor == "human-demo-approver" else actor)
            self.event(db, session, actor, "indicative_credit_decision",
                       {"decision": decision, "memo": state["memo"]["id"]})
            self.save(db, session, state)
            return state["memo"]

    def audit(self):
        with self.db() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM bank_events ORDER BY seq")]
        previous, valid = "0" * 64, True
        for row in rows:
            raw = json.dumps([row["at"], row["session"], row["actor"], row["operation"],
                              row["detail"], previous], ensure_ascii=False)
            valid = valid and row["previous"] == previous and row["hash"] == hashlib.sha256(raw.encode()).hexdigest()
            previous = row["hash"]
            row["detail"] = json.loads(row["detail"])
        return {"events": rows, "chain_valid": valid, "externally_anchored": False}
