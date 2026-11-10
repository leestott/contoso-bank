"""Banking policy regression coverage; no Azure resources or model calls."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from banking import Bank, CUSTOMERS, OFFERS
from commerce import DomainError


class BankingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 11, 10, tzinfo=timezone.utc)
        self.bank = Bank(Path(self.temp.name) / "bank.db", clock=lambda: self.now)

    def session(self, customer, verified=True):
        session = self.bank.start(customer)["session"]
        if verified:
            self.bank.action(session, "verify_start", {"date_of_birth": CUSTOMERS[customer]["dob"]})
            otp = self.bank.view(session)["messages"][-1]["text"]
            self.assertTrue(self.bank.action(session, "verify_complete", {"otp": otp})["verified"])
        return session

    def mandate(self, session):
        return self.bank.action(session, "create_mandate",
                                {"confirmed": True, "passcode": "246810"})["mandate"]

    def test_selected_start_locale_and_customer_defaults(self):
        for customer, fixture in CUSTOMERS.items():
            self.assertEqual(self.bank.view(self.bank.start(customer)["session"])["locale"], fixture["locale"])
            for locale in ("en-GB", "ru-RU", "az-AZ"):
                with self.subTest(customer=customer, locale=locale):
                    self.assertEqual(self.bank.view(self.bank.start(customer, locale)["session"])["locale"], locale)

    def test_invalid_start_locale_cannot_create_a_session(self):
        for locale in ("fr-FR", "", [], {}):
            with self.subTest(locale=locale), self.assertRaises(DomainError):
                self.bank.start("leyla", locale)
        with self.bank.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bank_sessions").fetchone()[0], 0)

    def test_language_change_preserves_verified_banking_state(self):
        session = self.session("james")
        mandate = self.mandate(session)
        for locale in ("ru-RU", "az-AZ", "en-GB"):
            self.bank.action(session, "set_locale", {"locale": locale})
            state = self.bank.view(session)
            self.assertEqual(state["locale"], locale)
            self.assertTrue(state["verified"])
            self.assertEqual(state["mandate"], mandate)

    def test_verification_required_and_otp_single_use(self):
        s = self.session("leyla", False)
        self.assertIn("error", self.bank.action(s, "freeze_card"))
        self.bank.action(s, "verify_start", {"date_of_birth": CUSTOMERS["leyla"]["dob"]})
        otp = self.bank.view(s)["messages"][-1]["text"]
        self.assertTrue(self.bank.action(s, "verify_complete", {"otp": otp})["verified"])
        self.assertIn("error", self.bank.action(s, "verify_complete", {"otp": otp}))

    def test_otp_expiry_and_lockout(self):
        s = self.session("leyla", False)
        self.bank.action(s, "verify_start", {"date_of_birth": CUSTOMERS["leyla"]["dob"]})
        otp = self.bank.view(s)["messages"][-1]["text"]
        self.now += timedelta(minutes=3)
        self.assertIn("error", self.bank.action(s, "verify_complete", {"otp": otp}))
        for _ in range(5):
            self.bank.action(s, "verify_start", {"date_of_birth": "wrong"})
        self.assertIn("locked", self.bank.action(
            s, "verify_start", {"date_of_birth": CUSTOMERS["leyla"]["dob"]})["error"])

    def test_lost_card_and_isolation(self):
        s, other = self.session("leyla"), self.session("leyla")
        self.assertIn("error", self.bank.action(s, "replace_card"))
        self.bank.action(s, "freeze_card")
        self.assertEqual(len(self.bank.action(s, "transactions")["transactions"]), 3)
        self.assertEqual(self.bank.action(s, "replace_card")["replacement_to"], CUSTOMERS["leyla"]["address"])
        self.assertEqual(self.bank.action(s, "virtual_card")["virtual_card"], "ready")
        self.assertIn("error", self.bank.action(s, "unfreeze_card"))
        self.assertEqual(self.bank.view(other)["card"], "active")

    def test_memo_calculation_and_exclusively_human_decision(self):
        s = self.session("dmitri")
        self.assertEqual(len(self.bank.action(s, "statements")["statements"]), 12)
        memo = self.bank.action(s, "prepare_memo", {"amount": 50000})["memo"]
        self.assertEqual(len(memo["pages"]), 2)
        self.assertAlmostEqual(memo["debt_service_coverage"],
                               memo["monthly_cash_flow_qapik"] / memo["monthly_payment_qapik"], places=3)
        self.assertIn("error", self.bank.action(s, "approve_credit"))
        self.assertEqual(self.bank.view(s)["memo"]["status"], "awaiting_human")
        with self.assertRaises(DomainError):
            self.bank.decide(s, "indicative_offer", False)
        self.assertEqual(self.bank.decide(s, "indicative_offer", True)["status"], "indicative_offer")
        with self.assertRaises(DomainError):
            self.bank.decide(s, "indicative_offer", True)

    def test_payment_scene_exact_mandate_and_double_block(self):
        s = self.session("james")
        m = self.mandate(s)
        self.assertEqual((m["per_transaction"], m["monthly"], len(m["merchants"])), (30000, 50000, 2))
        self.assertEqual(datetime.fromisoformat(m["expires"]) - datetime.fromisoformat(m["created"]),
                         timedelta(days=30))
        for offer in ["renewal-existing", "electricity"]:
            self.assertIn("payment", self.bank.action(s, "pay", {"offer_id": offer, "mandate_id": m["id"]}))
        blocked = self.bank.action(s, "pay", {"offer_id": "flight-upgrade", "mandate_id": m["id"]})
        self.assertTrue(blocked["blocked"])
        self.assertTrue({"unapproved_merchant", "per_transaction_limit"}.issubset(blocked["reasons"]))
        request = self.bank.action(s, "one_off_request",
                                   {"offer_id": "flight-upgrade", "mandate_id": m["id"]})["request"]
        self.assertTrue(request["does_not_override_mandate"])
        self.assertEqual(self.bank.view(s)["mandate"], m)
        self.assertEqual(len(self.bank.view(s)["payments"]), 2)

    def test_concurrent_idempotency_expiry_and_monthly_total(self):
        s = self.session("james")
        m = self.mandate(s)
        def pay(_):
            return self.bank.action(s, "pay", {"offer_id": "electricity", "mandate_id": m["id"]})
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(pay, range(4)))
        self.assertEqual(sum("payment" in result for result in results), 1)
        self.now += timedelta(days=30)
        result = self.bank.action(s, "pay", {"offer_id": "renewal-existing", "mandate_id": m["id"]})
        self.assertIn("mandate_expired", result["reasons"])

    def test_exact_transaction_and_monthly_limits(self):
        s = self.session("james")
        m = self.mandate(s)
        extra = {
            "bill-a": {"id": "bill-a", "merchant": "Baku Demo Utilities", "amount": 30000},
            "bill-b": {"id": "bill-b", "merchant": "Baku Demo Utilities", "amount": 20000},
            "bill-c": {"id": "bill-c", "merchant": "Baku Demo Utilities", "amount": 1},
        }
        with patch.dict(OFFERS, extra):
            for offer in ["bill-a", "bill-b"]:
                self.assertIn("payment", self.bank.action(
                    s, "pay", {"offer_id": offer, "mandate_id": m["id"]}))
            result = self.bank.action(s, "pay", {"offer_id": "bill-c", "mandate_id": m["id"]})
            self.assertEqual(result["reasons"], ["monthly_limit"])
        self.assertEqual(sum(p["amount"] for p in self.bank.view(s)["payments"]), 50000)

    def test_malformed_payload_fields_fail_explicitly(self):
        with self.assertRaises(DomainError):
            self.bank.start([])
        s = self.session("james")
        self.assertIn("error", self.bank.action(s, "set_locale", {"locale": []}))
        self.assertIn("error", self.bank.action(s, "pay", {"offer_id": []}))
        with self.assertRaises(DomainError):
            self.bank.decide(s, [], True)

    def test_cross_customer_refusal_is_flagged_and_audited(self):
        s = self.session("leyla")
        refused = self.bank.action(s, "profile", {"customer_id": "james"})
        self.assertTrue(refused["flagged"])
        audit = self.bank.audit()
        self.assertTrue(audit["chain_valid"])
        self.assertEqual(audit["events"][-1]["operation"], "cross_customer_or_policy_override_refused")
        self.assertNotIn("otp_hash", self.bank.view(s))

    def test_persistence_handoff_and_language_switch(self):
        s = self.session("dmitri")
        self.bank.action(s, "set_locale", {"locale": "en-GB"})
        self.bank.transcript(s, "caller", "Please switch to English.")
        self.assertEqual(self.bank.action(s, "handoff")["status"], "queued")
        reopened = Bank(self.bank.path)
        self.assertEqual(reopened.view(s)["locale"], "en-GB")
        self.assertEqual(len(reopened.view(s)["transcript"]), 1)

    def test_transcript_translation_is_stored_once_and_redacted(self):
        import transcripts
        s = self.session("dmitri")
        self.bank.action(s, "set_locale", {"locale": "ru-RU"})
        entry = self.bank.transcript(s, "caller", "Код 1234 56")
        self.assertEqual(entry["translation"], "pending")
        self.assertIsNone(entry["english"])
        self.assertTrue(self.bank.translate(s, entry["id"], "Code [number withheld]", "Foundry test"))
        self.assertFalse(self.bank.translate(s, entry["id"], "again", "Foundry test"))
        stored = self.bank.view(s)["transcript"][-1]
        self.assertEqual((stored["english"], stored["translation"]), ("Code [number withheld]", "Foundry test"))
        failed = self.bank.transcript(s, "agent", "Привет")
        self.assertTrue(self.bank.translate(s, failed["id"], None, "unavailable"))
        self.assertEqual(self.bank.view(s)["transcript"][-1]["translation"], "unavailable")
        self.assertEqual(transcripts.redact("OTP 12 34 56, amount 50,000"), "OTP [number withheld], amount 50,000")

    def test_transcript_translation_sends_only_redacted_text(self):
        import transcripts

        class Client:
            config = {"model_deployment": "judge"}
            body = None

            def request(self, method, path, body):
                Client.body = body
                return {"status": "completed", "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": "My code is 123456"}]}]}

        self.assertEqual(transcripts.english(Client(), "en-GB", "Hello"), ("Hello", "original"))
        value, source = transcripts.english(Client(), "az-AZ", "Kodum 123456")
        self.assertNotIn("123456", Client.body["input"])
        self.assertEqual(value, "My code is [number withheld]")
        self.assertIn("judge", source)


if __name__ == "__main__":
    unittest.main()
