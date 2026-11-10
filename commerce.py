"""Deterministic purchase authority. The model never supplies prices or verdicts."""

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

CATALOG = [
    {"id": "hs-45", "category": "headsets", "name": "Everyday USB headset",
     "merchant": "Northstar Supply", "price": 4500, "detail": "USB-C, noise-reducing microphone"},
    {"id": "hs-68", "category": "headsets", "name": "Wireless team headset",
     "merchant": "Contoso Workplace", "price": 6800, "detail": "Wireless, charging stand"},
    {"id": "hs-29", "category": "headsets", "name": "Budget call headset",
     "merchant": "Outlet Direct", "price": 2900, "detail": "USB-A, wired"},
    {"id": "mn-229", "category": "monitors", "name": "27-inch workplace monitor",
     "merchant": "Northstar Supply", "price": 22900, "detail": "QHD, height-adjustable stand"},
    {"id": "mn-349", "category": "monitors", "name": "32-inch design monitor",
     "merchant": "Contoso Workplace", "price": 34900, "detail": "4K, USB-C display input"},
    {"id": "mn-149", "category": "monitors", "name": "27-inch outlet monitor",
     "merchant": "Outlet Direct", "price": 14900, "detail": "Full HD, fixed stand"},
    {"id": "dk-79", "category": "docks", "name": "USB-C desk dock",
     "merchant": "Northstar Supply", "price": 7900, "detail": "HDMI, Ethernet, four USB ports"},
    {"id": "dk-119", "category": "docks", "name": "Dual-display desk dock",
     "merchant": "Contoso Workplace", "price": 11900, "detail": "Two display outputs, 100W charging"},
]
PRODUCTS = {p["id"]: p for p in CATALOG}
DEFAULT_POLICY = {"auto_limit": 10000, "transaction_limit": 50000, "daily_limit": 100000,
                  "merchants": ["Northstar Supply", "Contoso Workplace"],
                  "always_approve": False}


class DomainError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise DomainError(f"{name} must be an integer from {minimum} to {maximum}.")
    return value


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY, created TEXT NOT NULL, body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY, timestamp TEXT NOT NULL,
                    body TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL
                );
            """)
            db.execute("INSERT OR IGNORE INTO policy VALUES(1,?)", (json.dumps(DEFAULT_POLICY),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def policy(self, db):
        return json.loads(db.execute("SELECT body FROM policy WHERE id=1").fetchone()[0])

    def orders(self, db):
        return [json.loads(row[0]) for row in db.execute("SELECT body FROM orders ORDER BY created DESC")]

    def spent(self, db):
        # A reversal restores budget on the original UTC purchase day.
        today = utcnow()[:10]
        return sum(o["total"] for o in self.orders(db)
                   if o["status"] == "purchased" and o["purchased_at"][:10] == today)

    def verdict(self, db, product, quantity, approved=False):
        policy = self.policy(db)
        total = product["price"] * quantity
        checks = [
            {"name": "Merchant allowlist", "pass": product["merchant"] in policy["merchants"],
             "detail": product["merchant"]},
            {"name": "Transaction ceiling", "pass": total <= policy["transaction_limit"],
             "detail": f"Limit GBP {policy['transaction_limit'] / 100:,.2f}"},
            {"name": "UTC daily budget", "pass": self.spent(db) + total <= policy["daily_limit"],
             "detail": f"Remaining GBP {(policy['daily_limit'] - self.spent(db)) / 100:,.2f}"},
        ]
        blocked = any(not c["pass"] for c in checks)
        needs_approval = policy["always_approve"] or total > policy["auto_limit"]
        checks.append({"name": "Human approval", "pass": approved or not needs_approval,
                       "detail": "Always required" if policy["always_approve"]
                       else f"Required above GBP {policy['auto_limit'] / 100:,.2f}"})
        status = "blocked" if blocked else ("approval_required" if needs_approval and not approved else "ready")
        return {"status": status, "checks": checks, "total": total}

    def event(self, db, kind, order_id, actor, detail):
        body = json.dumps({"kind": kind, "order_id": order_id, "actor": actor,
                           "detail": detail}, sort_keys=True, separators=(",", ":"))
        row = db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        previous = row[0] if row else "0" * 64
        timestamp = utcnow()
        digest = hashlib.sha256((previous + timestamp + body).encode()).hexdigest()
        db.execute("INSERT INTO events(timestamp,body,previous_hash,hash) VALUES(?,?,?,?)",
                   (timestamp, body, previous, digest))

    def write_order(self, db, order):
        db.execute("UPDATE orders SET body=? WHERE id=?", (json.dumps(order), order["id"]))

    def create(self, product_id, quantity, reason, source, request="", response_id=None):
        if not isinstance(product_id, str) or product_id not in PRODUCTS:
            raise DomainError("Select an existing catalog product.")
        integer(quantity, "Quantity", 1, 100)
        product = PRODUCTS[product_id]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            verdict = self.verdict(db, product, quantity)
            order = {"id": uuid.uuid4().hex[:12], "created": utcnow(), "product": product,
                     "quantity": quantity, "total": verdict["total"], "status": verdict["status"],
                     "reason": reason, "source": source, "request": request,
                     "response_id": response_id, "checks": verdict["checks"],
                     "approver": None, "purchased_at": None}
            db.execute("INSERT INTO orders VALUES(?,?,?)",
                       (order["id"], order["created"], json.dumps(order)))
            self.event(db, "recommendation", order["id"], source, reason)
            self.event(db, order["status"], order["id"], "Policy engine", verdict["checks"])
        return order

    def transition(self, order_id, action, acknowledged=False):
        if action not in ("approve", "purchase", "reverse", "reject"):
            raise DomainError("Unknown order action.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                raise DomainError("Order not found.", 404)
            order = json.loads(row[0])
            actor = "Demo operator"
            if action == "approve":
                if order["status"] != "approval_required":
                    raise DomainError("Only a pending approval can be approved.", 409)
                if acknowledged is not True:
                    raise DomainError("Acknowledge this simulated purchase before approving.")
                verdict = self.verdict(db, order["product"], order["quantity"], approved=True)
                if verdict["status"] == "blocked":
                    raise DomainError("Current policy blocks this order. Approval cannot override it.", 409)
                order["status"], order["approver"] = "approved", "Demo approver"
                order["approved_at"] = utcnow()
                order["checks"] = verdict["checks"]
                actor = "Demo approver"
            elif action == "purchase":
                if order["status"] not in ("ready", "approved"):
                    raise DomainError("This order is not authorised for purchase.", 409)
                verdict = self.verdict(db, order["product"], order["quantity"],
                                       approved=order["status"] == "approved")
                if verdict["status"] != "ready":
                    raise DomainError("Policy or budget changed. Create a new recommendation.", 409)
                order["status"], order["purchased_at"] = "purchased", utcnow()
                order["checks"] = verdict["checks"]
                actor = "Simulated payment adapter"
            elif action == "reverse":
                if order["status"] != "purchased":
                    raise DomainError("Only a purchased order can be reversed once.", 409)
                order["status"], order["reversed_at"] = "reversed", utcnow()
                actor = "Simulated refund adapter"
            else:
                if order["status"] not in ("approval_required", "ready", "approved"):
                    raise DomainError("This order cannot be rejected.", 409)
                order["status"] = "rejected"
            self.write_order(db, order)
            self.event(db, action, order["id"], actor,
                       {"total": order["total"], "simulated": True, "status": order["status"]})
        return order

    def update_policy(self, policy):
        if not isinstance(policy, dict) or set(policy) != set(DEFAULT_POLICY):
            raise DomainError("All policy fields are required; unknown fields are not accepted.")
        for field in ("auto_limit", "transaction_limit", "daily_limit"):
            integer(policy[field], field, 0, 10000000)
        if not policy["auto_limit"] <= policy["transaction_limit"] <= policy["daily_limit"]:
            raise DomainError("Limits must satisfy: autonomous <= transaction <= daily budget.")
        merchants = policy["merchants"]
        if (not isinstance(merchants, list) or not all(isinstance(m, str) for m in merchants)
                or len(set(merchants)) != len(merchants)
                or not set(merchants) <= {p["merchant"] for p in CATALOG}):
            raise DomainError("Choose merchants from the synthetic catalog.")
        if type(policy["always_approve"]) is not bool:
            raise DomainError("Always approve must be true or false.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE policy SET body=? WHERE id=1", (json.dumps(policy),))
            self.event(db, "policy_updated", None, "Demo policy owner", policy)

    def state(self):
        with self.connect() as db:
            events, previous, valid = [], "0" * 64, True
            for row in db.execute("SELECT * FROM events ORDER BY seq"):
                expected = hashlib.sha256((previous + row["timestamp"] + row["body"]).encode()).hexdigest()
                valid = valid and row["previous_hash"] == previous and row["hash"] == expected
                events.append({"seq": row["seq"], "timestamp": row["timestamp"],
                               **json.loads(row["body"]), "hash": row["hash"]})
                previous = row["hash"]
            return {"catalog": CATALOG, "policy": self.policy(db), "orders": self.orders(db),
                    "spent": self.spent(db), "events": events, "audit_valid": valid}
