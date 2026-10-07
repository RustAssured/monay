"""Unit economics + Monte-Carlo simulator for the flakehunter hosted tier.

All money in USD per month. Every assumption lives in ASSUMPTIONS with a
source/label; change them there. Run:  python -m sim.model
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from typing import Dict

import numpy as np

# ---------------------------------------------------------------- assumptions
# (low, mode, high) triangular ranges unless noted.  [S]=sourced public price,
# [B]=published industry benchmark (verify before launch), [A]=my assumption.
ASSUMPTIONS = {
    # funnel
    "new_free_orgs_per_month": ((5, 25, 120), "[A] new orgs installing the hosted GitHub App / month. Deliberately "
                                "conservative: crowded market (BuildPulse, Trunk, Datadog, CircleCI). THE key unknown."),
    "free_to_paid": ((0.005, 0.02, 0.05), "[B] share of new free orgs that ever convert. Lenny Rachitsky's "
                     "2022 freemium survey: 3-5% good, 6-8% great for self-serve; dev tools set lower here."),
    "monthly_churn": ((0.025, 0.05, 0.08), "[B] SMB SaaS logo churn 3-7%/mo (Recurly / ChartMogul SMB benchmarks)."),
    "business_mix": ((0.05, 0.15, 0.25), "[A] share of paid orgs on Business ($99) vs Team ($29)."),
    "overage_per_paid": ((0.0, 2.0, 6.0), "[A] avg overage $/paid org/month (capped by customer spend limit)."),
    # costs
    "hosting_fixed": ((25, 40, 80), "[S] Hetzner/DO VPS + managed Postgres + backups + domain + email "
                      "(~EUR5-40 list prices); [A] total."),
    "hosting_per_paid": ((0.1, 0.3, 1.0), "[A] storage/CPU per paid org (XML history is small)."),
    "operator_hours": ((4, 8, 16), "[A] support + maintenance hours/month."),
    "hourly_rate": ((30, 50, 80), "[A] opportunity cost of operator time, $/h."),
}
PRICES = {"team": 29.0, "business": 99.0}
STRIPE_PCT, STRIPE_FIXED = 0.029, 0.30      # [S] stripe.com/pricing US cards
STRIPE_BILLING_PCT = 0.007                  # [S] Stripe Billing pay-as-you-go
STRIPE_TAX_PCT = 0.005                      # [S] Stripe Tax per-transaction
INCOME_TAX = 0.25                           # [A] effective tax on positive profit
LAUNCH_HOURS = 40                           # [A] one-off: landing page, GitHub App, ToS, Stripe setup
HORIZON = 36                                # months
KILL_MONTH, KILL_MIN_PAID = 9, 5            # stop-loss: shut down if <5 paying orgs at month 9


def mode(name):
    return ASSUMPTIONS[name][0][1]


@dataclass(frozen=True)
class Params:
    new_free_orgs_per_month: float
    free_to_paid: float
    monthly_churn: float
    business_mix: float
    overage_per_paid: float
    hosting_fixed: float
    hosting_per_paid: float
    operator_hours: float
    hourly_rate: float

    @classmethod
    def base(cls):
        return cls(**{k: mode(k) for k in ASSUMPTIONS})


def arpa(p: Params) -> float:
    return (1 - p.business_mix) * PRICES["team"] + p.business_mix * PRICES["business"] + p.overage_per_paid


def net_per_customer(p: Params) -> float:
    """Monthly contribution of one paying org after payment fees & variable hosting."""
    a = arpa(p)
    fees = a * (STRIPE_PCT + STRIPE_BILLING_PCT + STRIPE_TAX_PCT) + STRIPE_FIXED
    return a - fees - p.hosting_per_paid


def fixed_cost(p: Params, include_time=True) -> float:
    return p.hosting_fixed + (p.operator_hours * p.hourly_rate if include_time else 0.0)


def breakeven_customers(p: Params, include_time=True) -> float:
    return fixed_cost(p, include_time) / net_per_customer(p)


def simulate_path(p: Params, rng=None, months=HORIZON, kill_rule=True, include_time=True):
    """Return monthly arrays (paid customers, pre-tax profit). Stochastic if rng given."""
    paid = 0.0
    paid_hist, profit_hist = [], []
    alive = True
    for m in range(1, months + 1):
        if not alive:
            paid_hist.append(0); profit_hist.append(0.0); continue
        if rng is None:
            new_paid = p.new_free_orgs_per_month * p.free_to_paid
            churned = paid * p.monthly_churn
        else:
            new_free = rng.poisson(p.new_free_orgs_per_month)
            new_paid = rng.binomial(new_free, p.free_to_paid)
            churned = rng.binomial(int(paid), p.monthly_churn)
        paid = max(0.0, paid + new_paid - churned)
        profit = paid * net_per_customer(p) - fixed_cost(p, include_time)
        if m == 1:
            profit -= LAUNCH_HOURS * p.hourly_rate if include_time else 0.0
        paid_hist.append(paid); profit_hist.append(profit)
        if kill_rule and m == KILL_MONTH and paid < KILL_MIN_PAID:
            alive = False
    return np.array(paid_hist), np.array(profit_hist)


def after_tax(total_pre_tax: float) -> float:
    return total_pre_tax * (1 - INCOME_TAX) if total_pre_tax > 0 else total_pre_tax


def sample_params(rng) -> Params:
    vals = {k: rng.triangular(*ASSUMPTIONS[k][0]) for k in ASSUMPTIONS}
    return Params(**vals)


def monte_carlo(n=5000, seed=7, kill_rule=True, include_time=True) -> Dict:
    rng = np.random.default_rng(seed)
    totals, be_month, final_paid, final_mrr_profit = [], [], [], []
    for _ in range(n):
        p = sample_params(rng)
        paid, profit = simulate_path(p, rng, kill_rule=kill_rule, include_time=include_time)
        totals.append(after_tax(profit.sum()))
        pos = np.nonzero(profit > 0)[0]
        be_month.append(int(pos[0]) + 1 if len(pos) else math.inf)
        final_paid.append(paid[-1])
        final_mrr_profit.append(profit[-1])
    totals = np.array(totals); be = np.array(be_month); fp = np.array(final_paid)
    q = lambda a, x: float(np.percentile(a, x))
    return {
        "n": n, "kill_rule": kill_rule, "include_operator_time": include_time,
        "p_cum_profit_positive_36m": float((totals > 0).mean()),
        "cum_net_36m_after_tax": {"p10": q(totals, 10), "p50": q(totals, 50), "p90": q(totals, 90),
                                  "mean": float(totals.mean()), "min": float(totals.min())},
        "p_monthly_breakeven_by_36m": float(np.isfinite(be).mean()),
        "median_breakeven_month_if_reached": float(np.median(be[np.isfinite(be)])) if np.isfinite(be).any() else None,
        "paid_orgs_month36": {"p10": q(fp, 10), "p50": q(fp, 50), "p90": q(fp, 90)},
    }


def tornado(include_time=True) -> list:
    """One-at-a-time sensitivity of deterministic 36-month cumulative pre-tax profit."""
    base = Params.base()
    base_total = simulate_path(base, None, kill_rule=False, include_time=include_time)[1].sum()
    rows = []
    for k, ((lo, _, hi), _) in ASSUMPTIONS.items():
        tl = simulate_path(replace(base, **{k: lo}), None, kill_rule=False, include_time=include_time)[1].sum()
        th = simulate_path(replace(base, **{k: hi}), None, kill_rule=False, include_time=include_time)[1].sum()
        rows.append({"param": k, "low": lo, "high": hi, "total_at_low": round(tl), "total_at_high": round(th),
                     "swing": round(abs(th - tl))})
    rows.sort(key=lambda r: -r["swing"])
    return [{"base_total": round(base_total)}] + rows


def required_new_free_orgs(p: Params, include_time=True) -> float:
    """Steady-state funnel needed for break-even: paid* = new*conv/churn."""
    return breakeven_customers(p, include_time) * p.monthly_churn / p.free_to_paid


def report() -> Dict:
    b = Params.base()
    return {
        "base_params": b.__dict__,
        "arpa": round(arpa(b), 2),
        "net_per_customer": round(net_per_customer(b), 2),
        "breakeven_customers_incl_time": round(breakeven_customers(b, True), 1),
        "breakeven_customers_cash_only": round(breakeven_customers(b, False), 1),
        "required_new_free_orgs_per_month_incl_time": round(required_new_free_orgs(b, True), 1),
        "required_new_free_orgs_per_month_cash_only": round(required_new_free_orgs(b, False), 1),
        "steady_state_paid_base": round(b.new_free_orgs_per_month * b.free_to_paid / b.monthly_churn, 1),
        "mc_with_kill_rule_incl_time": monte_carlo(kill_rule=True, include_time=True),
        "mc_no_kill_rule_incl_time": monte_carlo(kill_rule=False, include_time=True),
        "mc_with_kill_rule_cash_only": monte_carlo(kill_rule=True, include_time=False),
        "tornado_incl_time": tornado(True),
    }


if __name__ == "__main__":
    r = report()
    out = json.dumps(r, indent=2, default=lambda o: None if o == math.inf else o)
    print(out)
    with open(__file__.replace("model.py", "results.json"), "w") as f:
        f.write(out + "\n")
