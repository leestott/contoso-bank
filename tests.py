import concurrent.futures
import copy
import http.client
import json
import logging
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from app import handler_for
from commerce import CATALOG, DEFAULT_POLICY, DomainError, Store
from foundry import Foundry, FoundryError


def isolate_settings(test):
    source = patch("settings.ENV_FILE", Path(test.tmp.name) / "missing.env")
    environment = patch.dict("os.environ", {
        "COMMERCE_AGENT_NAME": "test-agent",
        "COMMERCE_FOUNDRY_ENDPOINT": "https://example.services.ai.azure.com/api/projects/test-project",
    }, clear=True)
    source.start()
    environment.start()
    test.addCleanup(source.stop)
    test.addCleanup(environment.stop)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def order(self, product="hs-45", quantity=1):
        return self.store.create(product, quantity, "Synthetic test", "Test")

    def test_autonomous_boundary_is_inclusive(self):
        policy = copy.deepcopy(DEFAULT_POLICY)
        policy["auto_limit"] = 9000
        self.store.update_policy(policy)
        self.assertEqual(self.order(quantity=2)["status"], "ready")
        self.assertEqual(self.order(quantity=3)["status"], "approval_required")

    def test_zero_autonomy_and_explicit_always_approve(self):
        policy = copy.deepcopy(DEFAULT_POLICY)
        policy["auto_limit"] = 0
        self.store.update_policy(policy)
        self.assertEqual(self.order()["status"], "approval_required")
        policy = copy.deepcopy(DEFAULT_POLICY)
        policy["always_approve"] = True
        self.store.update_policy(policy)
        self.assertEqual(self.order()["status"], "approval_required")

    def test_blocked_merchant_cannot_be_approved_or_purchased(self):
        order = self.order("hs-29")
        self.assertEqual(order["status"], "blocked")
        for action in ("approve", "purchase"):
            with self.assertRaises(DomainError):
                self.store.transition(order["id"], action, True)

    def test_approval_is_explicit_and_never_charges(self):
        order = self.order("mn-229")
        self.assertEqual(order["status"], "approval_required")
        with self.assertRaises(DomainError):
            self.store.transition(order["id"], "purchase")
        with self.assertRaises(DomainError):
            self.store.transition(order["id"], "approve")
        order = self.store.transition(order["id"], "approve", True)
        self.assertEqual(order["status"], "approved")
        self.assertEqual(self.store.state()["spent"], 0)
        order = self.store.transition(order["id"], "purchase")
        self.assertEqual(order["status"], "purchased")
        self.assertEqual(self.store.state()["spent"], 22900)

    def test_purchase_and_refund_are_once_only(self):
        order = self.order()
        self.store.transition(order["id"], "purchase")
        with self.assertRaises(DomainError):
            self.store.transition(order["id"], "purchase")
        self.store.transition(order["id"], "reverse")
        self.assertEqual(self.store.state()["spent"], 0)
        with self.assertRaises(DomainError):
            self.store.transition(order["id"], "reverse")
        with self.assertRaises(DomainError):
            self.store.transition(order["id"], "purchase")

    def test_policy_change_is_enforced_at_checkout(self):
        order = self.order()
        policy = copy.deepcopy(DEFAULT_POLICY)
        policy["merchants"] = []
        self.store.update_policy(policy)
        with self.assertRaises(DomainError):
            self.store.transition(order["id"], "purchase")
        self.assertEqual(self.store.state()["spent"], 0)

    def test_new_approval_requirement_cannot_be_bypassed(self):
        order = self.order()
        policy = copy.deepcopy(DEFAULT_POLICY)
        policy["always_approve"] = True
        self.store.update_policy(policy)
        with self.assertRaises(DomainError):
            self.store.transition(order["id"], "purchase")

    def test_approved_order_cannot_bypass_new_hard_limit(self):
        order = self.order("mn-229")
        self.store.transition(order["id"], "approve", True)
        policy = copy.deepcopy(DEFAULT_POLICY)
        policy["transaction_limit"] = 20000
        self.store.update_policy(policy)
        with self.assertRaises(DomainError):
            self.store.transition(order["id"], "purchase")

    def test_pending_order_cannot_override_new_merchant_policy(self):
        order = self.order("mn-229")
        policy = copy.deepcopy(DEFAULT_POLICY)
        policy["merchants"] = ["Contoso Workplace"]
        self.store.update_policy(policy)
        with self.assertRaises(DomainError):
            self.store.transition(order["id"], "approve", True)

    def test_transaction_ceiling_and_exact_budget(self):
        self.assertEqual(self.order("mn-349", 2)["status"], "blocked")
        policy = copy.deepcopy(DEFAULT_POLICY)
        policy.update(auto_limit=9000, transaction_limit=9000, daily_limit=9000)
        self.store.update_policy(policy)
        order = self.order(quantity=2)
        self.assertEqual(order["status"], "ready")
        self.store.transition(order["id"], "purchase")
        self.assertEqual(self.order()["status"], "blocked")

    def test_concurrent_purchases_cannot_overspend(self):
        policy = copy.deepcopy(DEFAULT_POLICY)
        policy.update(auto_limit=5000, transaction_limit=5000, daily_limit=5000)
        self.store.update_policy(policy)
        orders = [self.order(), self.order()]

        def purchase(order):
            try:
                self.store.transition(order["id"], "purchase")
                return True
            except DomainError:
                return False

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(purchase, orders))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.store.state()["spent"], 4500)

    def test_rejected_order_cannot_move_money(self):
        order = self.order()
        self.store.transition(order["id"], "reject")
        with self.assertRaises(DomainError):
            self.store.transition(order["id"], "purchase")

    def test_invalid_quantity_and_product(self):
        for quantity in (True, 0, -1, 101, 1.5, "1", None):
            with self.subTest(quantity=quantity), self.assertRaises(DomainError):
                self.order(quantity=quantity)
        for product in ("invented", [], None):
            with self.subTest(product=product), self.assertRaises(DomainError):
                self.order(product)

    def test_invalid_policy(self):
        for change in ({"auto_limit": -1}, {"auto_limit": 60000},
                       {"daily_limit": True}, {"merchants": "Northstar Supply"},
                       {"merchants": ["Invented"]}, {"merchants": [[]]},
                       {"always_approve": "true"}, {"new_field": 1}):
            policy = copy.deepcopy(DEFAULT_POLICY)
            policy.update(change)
            with self.subTest(change=change), self.assertRaises(DomainError):
                self.store.update_policy(policy)

    def test_audit_and_restart_persistence(self):
        order = self.order()
        self.store.transition(order["id"], "purchase")
        reloaded = Store(Path(self.tmp.name) / "test.db").state()
        self.assertEqual(reloaded["spent"], 4500)
        self.assertTrue(reloaded["audit_valid"])
        self.assertEqual(len(reloaded["events"]), 3)
        with self.store.connect() as db:
            db.execute("UPDATE events SET body='{}' WHERE seq=1")
        self.assertFalse(self.store.state()["audit_valid"])


class FoundryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        isolate_settings(self)
        self.client = Foundry()

    def response(self, decision, status="completed"):
        return {"id": "test-response", "status": status, "output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(decision)}]}
        ]}

    def test_structured_recommendation_is_validated(self):
        with patch.object(self.client, "request", return_value=self.response(
                {"product_id": "hs-45", "reason": "Good value."})):
            self.assertEqual(self.client.recommend("Synthetic", CATALOG[:3], 1, DEFAULT_POLICY),
                             ("hs-45", "Good value.", "test-response"))

    def test_unknown_wrong_category_empty_and_incomplete_outputs_fail_closed(self):
        for decision in ({"product_id": "mn-229", "reason": "Wrong category"},
                         {"product_id": None, "reason": "No match"},
                         {"product_id": "hs-45", "reason": ""},
                         {"product_id": "hs-45", "reason": "x" * 601},
                         {"product_id": [], "reason": "Invalid type"}, [], {}):
            with self.subTest(decision=decision):
                with patch.object(self.client, "request", return_value=self.response(decision)):
                    with self.assertRaises(FoundryError):
                        self.client.recommend("Synthetic", CATALOG[:3], 1, DEFAULT_POLICY)
        with patch.object(self.client, "request", return_value=self.response({}, "incomplete")):
            with self.assertRaises(FoundryError):
                self.client.recommend("Synthetic", CATALOG, 1, DEFAULT_POLICY)

    def test_external_endpoint_is_rejected(self):
        config = dict(self.client.config, project_endpoint="https://example.com/api/projects/test")
        with self.assertRaises(ValueError):
            Foundry(config)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        isolate_settings(self)
        self.store = Store(Path(self.tmp.name) / "api.db")
        self.foundry = Foundry()
        from banking import Bank
        self.bank = Bank(Path(self.tmp.name) / "bank.db")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                         handler_for(self.store, self.foundry, "test-token", self.bank))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, body=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        supplied = {"Content-Type": "application/json", "X-Baku-Token": "test-token"}
        supplied.update(headers or {})
        data = raw if raw is not None else (json.dumps(body) if body is not None else None)
        connection.request("POST" if data is not None else "GET", path, data, supplied)
        response = connection.getresponse()
        status, content = response.status, response.read()
        connection.close()
        return status, json.loads(content)

    def test_state_and_rule_mode(self):
        status, state = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["csrf_token"], "test-token")
        status, result = self.request("/api/recommend", {
            "category": "headsets", "quantity": 1, "request": "Synthetic headset", "mode": "rules",
        })
        self.assertEqual(status, 201)
        self.assertEqual(result["order"]["source"], "Rule-based demo")
        self.assertEqual(result["order"]["total"], 4500)

    def test_public_config_omits_cloud_identity_and_optional_survey(self):
        status, config = self.request("/api/bank/config")
        self.assertEqual(status, 200)
        self.assertIsNone(config["survey"])
        self.assertEqual(config["project_state"], "Unknown")
        for key in ("subscription_id", "tenant_id", "project_endpoint", "account_name"):
            self.assertNotIn(key, config)
        self.assertIsNone(self.request("/api/state")[1]["survey"])

    def test_invalid_survey_returns_explicit_private_value_free_error(self):
        with patch.dict("os.environ", {"DEMO_SURVEY_URL": "http://private-value.test"}):
            for path in ("/api/state", "/api/bank/config"):
                status, config = self.request(path)
                self.assertEqual(status, 500)
                self.assertNotIn("private-value", config["error"])

    def test_untrusted_price_and_status_fields_are_ignored(self):
        status, result = self.request("/api/catalog-order", {
            "product_id": "hs-29", "quantity": 1, "price": 0, "status": "approved",
        })
        self.assertEqual(status, 201)
        self.assertEqual(result["order"]["status"], "blocked")
        self.assertEqual(result["order"]["total"], 2900)

    def test_live_error_does_not_fall_back_to_rules(self):
        with patch.object(self.foundry, "recommend", side_effect=FoundryError("Test failure")):
            status, result = self.request("/api/recommend", {
                "category": "headsets", "quantity": 1, "request": "Synthetic", "mode": "foundry",
            })
        self.assertEqual(status, 502)
        self.assertEqual(result["error"], "Test failure")
        self.assertEqual(self.store.state()["orders"], [])

    def test_csrf_origin_and_host(self):
        body = {"product_id": "hs-45", "quantity": 1}
        for headers in ({"X-Baku-Token": ""}, {"Origin": "https://example.com"},
                        {"Host": "attacker.example"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/catalog-order", body, headers)[0], 403)

    def test_bad_json_non_object_and_size(self):
        for raw in ("{", "[]", "null"):
            with self.subTest(raw=raw):
                self.assertEqual(self.request("/api/catalog-order", raw=raw)[0], 400)
        self.assertEqual(self.request("/api/catalog-order", raw="x" * 8193)[0], 413)

    def test_unknown_mode_is_not_offline_success(self):
        self.assertEqual(self.request("/api/recommend", {
            "category": "headsets", "quantity": 1, "request": "Synthetic", "mode": "unknown",
        })[0], 400)

    def test_banking_session_and_verification_boundary(self):
        status, started = self.request("/api/bank/start", {"customer": "leyla"})
        self.assertEqual(status, 201)
        status, result = self.request("/api/bank/action", {
            "session": started["session"], "operation": "freeze_card", "payload": {},
        })
        self.assertEqual(status, 200)
        self.assertIn("Verify", result["error"])
        self.assertEqual(self.request("/api/bank/state", {"session": "unknown"})[0], 401)
        self.assertEqual(self.request("/api/bank/start", {"customer": []})[0], 400)

    def test_banking_boundaries_and_no_credit_approval_tool(self):
        body = {"customer": "dmitri"}
        self.assertEqual(self.request("/api/bank/start", body, {"X-Baku-Token": ""})[0], 403)
        self.assertEqual(self.request("/api/bank/start", body, {"Origin": "https://example.com"})[0], 403)
        started = self.request("/api/bank/start", body)[1]
        result = self.request("/api/bank/action", {
            "session": started["session"], "operation": "approve_credit", "payload": {},
        })[1]
        self.assertIn("error", result)


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    suite = unittest.defaultTestLoader.loadTestsFromNames(["tests", "test_banking", "test_settings"])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
