"""Usage metering + billing stub for the hosted paid tier.

Stripe-COMPATIBLE payload shapes, TEST MODE ONLY. There is deliberately no
network code here: `FakeStripeTransport` records what would be sent. A human
operator wires a real transport later (see REPORT.md launch checklist).

Fair-billing rules enforced in code (and tested):
  * public/open-source repos are always free (no metering)
  * idempotent usage recording - a retried CI webhook never double-bills
  * hard spend cap per customer (opt-in overage, default cap = 0 overage)
  * cancel any time; entitlements stay until period end; data export stays
    available for 30 days after cancellation
  * all money is integer cents (no float rounding)
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

METER_EVENT_NAME = "ci_runs_analyzed"


class LiveKeyRefused(RuntimeError):
    pass


def assert_test_mode(api_key: str) -> None:
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("api key required")
    if api_key.startswith(("sk_live_", "rk_live_", "pk_live_")):
        raise LiveKeyRefused("live Stripe keys are refused by this stub; use sk_test_...")
    if not api_key.startswith(("sk_test_", "rk_test_")):
        raise ValueError("expected a Stripe test-mode secret key (sk_test_...)")


@dataclass(frozen=True)
class Plan:
    code: str
    monthly_cents: int
    included_runs: int
    overage_cents_per_1000: int
    max_private_repos: int
    history_days: int


PLANS: Dict[str, Plan] = {
    "free":     Plan("free", 0, 300, 0, 1, 14),
    "team":     Plan("team", 2900, 5_000, 500, 10, 90),
    "business": Plan("business", 9900, 30_000, 400, 50, 365),
}


@dataclass
class Customer:
    customer_id: str                 # Stripe-style "cus_..."
    plan: str = "free"
    overage_cap_cents: int = 0       # 0 = never bill above plan price
    status: str = "active"           # active | past_due | canceled
    period_end: float = 0.0
    canceled_at: Optional[float] = None


class FakeStripeTransport:
    """Records requests that WOULD be sent to api.stripe.com. Never does I/O."""

    def __init__(self, api_key: str):
        assert_test_mode(api_key)
        self.api_key = api_key
        self.requests: List[Tuple[str, str, dict, Optional[str]]] = []

    def post(self, path: str, body: dict, idempotency_key: Optional[str] = None) -> dict:
        self.requests.append(("POST", path, body, idempotency_key))
        return {"object": "billing.meter_event", "livemode": False, **body}


class UsageMeter:
    def __init__(self, transport: FakeStripeTransport):
        self.transport = transport
        self._seen: set = set()
        self.usage: Dict[Tuple[str, str], int] = {}  # (customer, period) -> runs

    @staticmethod
    def period_key(ts: float) -> str:
        return time.strftime("%Y-%m", time.gmtime(ts))

    def record(self, customer: Customer, repo_is_public: bool, runs: int,
               event_id: str, ts: Optional[float] = None) -> bool:
        """Record analysed CI runs. Returns True if billable usage was recorded."""
        if runs <= 0:
            raise ValueError("runs must be positive")
        if repo_is_public:
            return False                       # OSS is free, never metered
        if event_id in self._seen:
            return False                       # idempotent: retries don't double bill
        self._seen.add(event_id)
        ts = time.time() if ts is None else ts
        k = (customer.customer_id, self.period_key(ts))
        self.usage[k] = self.usage.get(k, 0) + runs
        # Shape of Stripe's POST /v1/billing/meter_events
        self.transport.post("/v1/billing/meter_events", {
            "event_name": METER_EVENT_NAME,
            "payload": {"stripe_customer_id": customer.customer_id, "value": str(runs)},
            "identifier": event_id,
            "timestamp": int(ts),
        }, idempotency_key=event_id)
        return True


def compute_invoice(customer: Customer, runs: int) -> dict:
    plan = PLANS[customer.plan]
    over_runs = max(0, runs - plan.included_runs)
    # pro-rata per run, rounded up to the next whole cent; integer math only
    raw_overage = -(-over_runs * plan.overage_cents_per_1000 // 1000)
    overage = min(raw_overage, customer.overage_cap_cents)
    lines = [{"description": f"{plan.code} plan", "amount": plan.monthly_cents}]
    if raw_overage:
        lines.append({"description": f"{over_runs} runs over {plan.included_runs} included",
                      "amount": overage,
                      "note": "capped by your spend limit" if overage < raw_overage else ""})
    return {"customer": customer.customer_id, "lines": lines,
            "total": sum(l["amount"] for l in lines), "currency": "usd",
            "uncapped_overage": raw_overage, "over_limit": over_runs > 0}


def entitled(customer: Customer, feature: str, now: Optional[float] = None) -> bool:
    """Paid features stay on until period end after cancel; past_due gets a grace period."""
    now = time.time() if now is None else now
    if feature == "export":
        if customer.canceled_at is None:
            return True
        return now <= max(customer.period_end, customer.canceled_at) + 30 * 86400
    if customer.plan == "free":
        return feature in ("cli", "public_repos")
    if customer.status == "canceled":
        return now <= customer.period_end
    if customer.status == "past_due":
        return now <= customer.period_end + 7 * 86400
    return True


# --- Webhooks -------------------------------------------------------------

def sign_webhook(payload: bytes, secret: str, ts: int) -> str:
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def verify_webhook(payload: bytes, header: str, secret: str, tolerance: int = 300,
                   now: Optional[float] = None) -> bool:
    """Stripe-Signature verification scheme (HMAC-SHA256 over 't.payload')."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
    except (ValueError, KeyError):
        return False
    now = time.time() if now is None else now
    if abs(now - ts) > tolerance:
        return False
    expected = sign_webhook(payload, secret, ts).split("v1=", 1)[1]
    sigs = [v for k, v in (p.split("=", 1) for p in header.split(",")) if k == "v1"]
    return any(hmac.compare_digest(expected, s) for s in sigs)


def apply_event(customer: Customer, event: dict) -> Customer:
    typ = event.get("type")
    obj = event.get("data", {}).get("object", {})
    if typ in ("customer.subscription.created", "customer.subscription.updated"):
        customer.plan = obj.get("metadata", {}).get("plan", customer.plan)
        customer.status = {"active": "active", "trialing": "active", "past_due": "past_due",
                           "canceled": "canceled"}.get(obj.get("status"), customer.status)
        customer.period_end = float(obj.get("current_period_end", customer.period_end))
    elif typ == "customer.subscription.deleted":
        customer.status = "canceled"
        customer.canceled_at = float(obj.get("canceled_at", time.time()))
    elif typ == "invoice.payment_failed":
        customer.status = "past_due"
    elif typ == "invoice.paid":
        customer.status = "active"
    return customer
