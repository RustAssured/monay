import json
import unittest

from flakehunter.billing import (PLANS, Customer, FakeStripeTransport, LiveKeyRefused, UsageMeter,
                                 apply_event, assert_test_mode, compute_invoice, entitled,
                                 sign_webhook, verify_webhook)

KEY = "sk_test_dummy"
DAY = 86400


class KeyTest(unittest.TestCase):
    def test_live_keys_refused(self):
        for k in ("sk_live_abc", "rk_live_x", "pk_live_y"):
            with self.assertRaises(LiveKeyRefused):
                FakeStripeTransport(k)
        with self.assertRaises(ValueError):
            assert_test_mode("garbage")
        assert_test_mode(KEY)


class MeterTest(unittest.TestCase):
    def setUp(self):
        self.t = FakeStripeTransport(KEY)
        self.m = UsageMeter(self.t)
        self.c = Customer("cus_123", plan="team")

    def test_idempotent_and_public_free(self):
        ts = 1_790_000_000
        self.assertTrue(self.m.record(self.c, False, 10, "evt_1", ts))
        self.assertFalse(self.m.record(self.c, False, 10, "evt_1", ts))   # retry
        self.assertFalse(self.m.record(self.c, True, 999, "evt_2", ts))   # OSS repo
        self.assertEqual(self.m.usage[("cus_123", self.m.period_key(ts))], 10)
        self.assertEqual(len(self.t.requests), 1)
        method, path, body, idem = self.t.requests[0]
        self.assertEqual((method, path, idem), ("POST", "/v1/billing/meter_events", "evt_1"))
        self.assertEqual(body["payload"], {"stripe_customer_id": "cus_123", "value": "10"})

    def test_rejects_nonpositive(self):
        with self.assertRaises(ValueError):
            self.m.record(self.c, False, 0, "e")


class InvoiceTest(unittest.TestCase):
    def test_within_plan(self):
        inv = compute_invoice(Customer("c", "team"), 100)
        self.assertEqual(inv["total"], 2900)
        self.assertFalse(inv["over_limit"])

    def test_overage_capped_by_default(self):
        inv = compute_invoice(Customer("c", "team"), PLANS["team"].included_runs + 2500)
        self.assertEqual(inv["uncapped_overage"], 1250)   # 2.5k runs * $5/1k
        self.assertEqual(inv["total"], 2900)              # default cap 0: never surprise-billed
        self.assertTrue(inv["over_limit"])

    def test_overage_with_opt_in_cap(self):
        c = Customer("c", "team", overage_cap_cents=1000)
        inv = compute_invoice(c, PLANS["team"].included_runs + 1)
        self.assertEqual(inv["total"], 2900 + 1)  # pro-rata: 0.5c rounds up to 1c, not a whole 1k block
        inv2 = compute_invoice(c, PLANS["team"].included_runs + 10_000)
        self.assertEqual(inv2["total"], 2900 + 1000)

    def test_free_plan_zero(self):
        self.assertEqual(compute_invoice(Customer("c"), 10**6)["total"], 0)


class EntitlementTest(unittest.TestCase):
    def test_cancel_keeps_access_until_period_end_and_export_30d(self):
        now = 1_790_000_000
        c = Customer("c", "team", period_end=now + 10 * DAY)
        apply_event(c, {"type": "customer.subscription.deleted", "data": {"object": {"canceled_at": now}}})
        self.assertEqual(c.status, "canceled")
        self.assertTrue(entitled(c, "pr_comments", now + 5 * DAY))
        self.assertFalse(entitled(c, "pr_comments", now + 11 * DAY))
        self.assertTrue(entitled(c, "export", now + 39 * DAY))
        self.assertFalse(entitled(c, "export", now + 41 * DAY))

    def test_past_due_grace(self):
        now = 1_790_000_000
        c = Customer("c", "team", period_end=now)
        apply_event(c, {"type": "invoice.payment_failed", "data": {"object": {}}})
        self.assertTrue(entitled(c, "pr_comments", now + 6 * DAY))
        self.assertFalse(entitled(c, "pr_comments", now + 8 * DAY))
        apply_event(c, {"type": "invoice.paid", "data": {"object": {}}})
        self.assertTrue(entitled(c, "pr_comments", now + 8 * DAY))

    def test_free_plan_features(self):
        c = Customer("c")
        self.assertTrue(entitled(c, "cli"))
        self.assertFalse(entitled(c, "pr_comments"))

    def test_subscription_update(self):
        c = Customer("c")
        apply_event(c, {"type": "customer.subscription.updated", "data": {"object": {
            "status": "trialing", "current_period_end": 123, "metadata": {"plan": "business"}}}})
        self.assertEqual((c.plan, c.status, c.period_end), ("business", "active", 123.0))


class WebhookTest(unittest.TestCase):
    def test_roundtrip_and_tamper(self):
        payload = json.dumps({"type": "invoice.paid"}).encode()
        h = sign_webhook(payload, "whsec_test", 1000)
        self.assertTrue(verify_webhook(payload, h, "whsec_test", now=1100))
        self.assertFalse(verify_webhook(payload + b" ", h, "whsec_test", now=1100))
        self.assertFalse(verify_webhook(payload, h, "whsec_other", now=1100))
        self.assertFalse(verify_webhook(payload, h, "whsec_test", now=1000 + 301))  # replay window
        self.assertFalse(verify_webhook(payload, "garbage", "whsec_test", now=1000))


if __name__ == "__main__":
    unittest.main()
