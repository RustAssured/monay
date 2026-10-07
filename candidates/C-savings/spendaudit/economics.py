"""Economics: (a) a household using the tool on its own data, (b) the tool sold as a product.

Every input is an explicit ASSUMPTION with a low / base / high range. Nothing here is measured market data.
Sources for anchoring are listed in REPORT.md and are approximate (verify before relying on them).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, fields, replace


# ---------------------------------------------------------------- (a) household use
@dataclass
class HouseholdAssumptions:
    expected_savings_per_year: float = 494.0   # confidence-weighted, from the synthetic benchmark (NOT real data)
    realisation_rate: float = 0.5               # extra haircut: fraction of "expected" a real user actually captures
    setup_hours: float = 1.5                    # export CSV/OFX from banks, run, read report, make calls
    monthly_hours: float = 0.25                 # monthly re-run
    value_of_time_per_hour: float = 25.0        # opportunity cost of the user's time
    tool_cost_per_year: float = 0.0             # open-source local use


HOUSEHOLD_RANGES = {
    "expected_savings_per_year": (150.0, 494.0, 1000.0),
    "realisation_rate": (0.25, 0.5, 0.8),
    "setup_hours": (0.75, 1.5, 4.0),
    "monthly_hours": (0.1, 0.25, 0.75),
    "value_of_time_per_hour": (10.0, 25.0, 60.0),
    "tool_cost_per_year": (0.0, 0.0, 36.0),
}


def household_net(a: HouseholdAssumptions) -> dict:
    gain = a.expected_savings_per_year * a.realisation_rate
    time_cost = (a.setup_hours + 12 * a.monthly_hours) * a.value_of_time_per_hour
    cost = time_cost + a.tool_cost_per_year
    return {"gain": gain, "time_cost": time_cost, "cash_cost": a.tool_cost_per_year, "net": gain - cost,
            "cash_net": gain - a.tool_cost_per_year,
            "breakeven_savings": cost / a.realisation_rate if a.realisation_rate else float("inf")}


# ---------------------------------------------------------------- (b) product
@dataclass
class ProductAssumptions:
    price_per_year: float = 36.0           # paid "Pro" tier; core stays free & open source
    payment_pct: float = 0.029             # card processing % (typical published US rate for Stripe-like processors)
    payment_fixed: float = 0.30            # per-charge fixed fee
    refund_rate: float = 0.08              # money-back guarantee: refund if it finds < price in savings
    support_hours_per_customer: float = 0.2
    operator_hourly_cost: float = 0.0      # 0 = founder time not cashed out (shown separately)
    fixed_costs_per_year: float = 1500.0   # domain, static hosting, code-signing certs, accounting, LLC fees
    free_users_year1: float = 3000.0       # organic: open-source repo, honest write-ups, community posts
    conversion_rate: float = 0.04          # free -> paid
    paid_marketing: float = 0.0            # cash spent on ads in year 1
    paid_cac: float = 40.0                 # cost per paying customer from ads
    annual_retention: float = 0.6          # for multi-year LTV


PRODUCT_RANGES = {
    "price_per_year": (24.0, 36.0, 60.0),
    "refund_rate": (0.03, 0.08, 0.20),
    "support_hours_per_customer": (0.05, 0.2, 0.5),
    "fixed_costs_per_year": (800.0, 1500.0, 3000.0),
    "free_users_year1": (500.0, 3000.0, 15000.0),
    "conversion_rate": (0.01, 0.04, 0.08),
    "annual_retention": (0.4, 0.6, 0.8),
}


def product_year1(a: ProductAssumptions) -> dict:
    organic_paid = a.free_users_year1 * a.conversion_rate
    ad_paid = a.paid_marketing / a.paid_cac if a.paid_cac else 0.0
    customers = organic_paid + ad_paid
    net_price = a.price_per_year * (1 - a.refund_rate) - (a.price_per_year * a.payment_pct + a.payment_fixed)
    contribution = net_price - a.support_hours_per_customer * a.operator_hourly_cost
    revenue = customers * a.price_per_year * (1 - a.refund_rate)
    net = customers * contribution - a.fixed_costs_per_year - a.paid_marketing
    be_customers = a.fixed_costs_per_year / contribution if contribution > 0 else float("inf")
    ltv = contribution / (1 - a.annual_retention) if a.annual_retention < 1 else float("inf")
    return {"customers": customers, "contribution_per_customer": contribution, "revenue": revenue, "net": net,
            "breakeven_customers": be_customers, "ltv": ltv, "ltv_to_cac": ltv / a.paid_cac if a.paid_cac else None,
            "support_hours": customers * a.support_hours_per_customer}


def sensitivity(base, ranges, fn, metric="net"):
    """One-at-a-time (tornado) sensitivity: vary each input to its low/high while others stay at base."""
    rows = []
    b = fn(base)[metric]
    for name, (lo, _, hi) in ranges.items():
        v_lo = fn(replace(base, **{name: lo}))[metric]
        v_hi = fn(replace(base, **{name: hi}))[metric]
        rows.append((name, lo, hi, v_lo, v_hi, abs(v_hi - v_lo)))
    rows.sort(key=lambda r: -r[5])
    return b, rows


def monte_carlo(base, ranges, fn, metric="net", n=20000, seed=7):
    """Triangular(low, base, high) draws for every ranged input. Output is only as good as the ranges."""
    rng = random.Random(seed)
    vals = []
    for _ in range(n):
        kw = {k: rng.triangular(lo, hi, mode) for k, (lo, mode, hi) in ranges.items()}
        vals.append(fn(replace(base, **kw))[metric])
    vals.sort()
    pct = lambda p: vals[min(n - 1, int(p * n))]
    return {"p05": pct(0.05), "p50": pct(0.50), "p95": pct(0.95), "mean": sum(vals) / n,
            "prob_positive": sum(v > 0 for v in vals) / n}


def format_economics(household_savings=None):
    ha = HouseholdAssumptions()
    if household_savings is not None:
        ha = replace(ha, expected_savings_per_year=household_savings)
    out = ["## Household use (assumption-driven)", ""]
    h = household_net(ha)
    out.append(f"Base: gain ${h['gain']:,.0f}/yr, time cost ${h['time_cost']:,.0f}/yr, cash cost "
               f"${h['cash_cost']:,.0f} -> net ${h['net']:,.0f}/yr (cash-only net ${h['cash_net']:,.0f}). "
               f"Break-even requires ${h['breakeven_savings']:,.0f}/yr of identified-and-expected savings.")
    b, rows = sensitivity(ha, HOUSEHOLD_RANGES, household_net)
    out += ["", "| input | low | high | net@low | net@high |", "|---|---|---|---|---|"]
    out += [f"| {r[0]} | {r[1]:g} | {r[2]:g} | {r[3]:,.0f} | {r[4]:,.0f} |" for r in rows]
    mc = monte_carlo(ha, HOUSEHOLD_RANGES, household_net)
    out.append(f"\nMonte Carlo (triangular priors, n=20000): p05 ${mc['p05']:,.0f}, median ${mc['p50']:,.0f}, "
               f"p95 ${mc['p95']:,.0f}; P(net>0 incl. time) = {mc['prob_positive']:.0%}.")
    mc_cash = monte_carlo(ha, HOUSEHOLD_RANGES, household_net, metric="cash_net")
    out.append(f"Cash-only (ignoring time): median ${mc_cash['p50']:,.0f}; P(cash net>0) = "
               f"{mc_cash['prob_positive']:.0%}.")

    pa = ProductAssumptions()
    p = product_year1(pa)
    out += ["", "## Product (year 1, assumption-driven)", "",
            f"Base: {p['customers']:.0f} paying customers x ${p['contribution_per_customer']:.2f} contribution "
            f"- ${pa.fixed_costs_per_year:,.0f} fixed = net ${p['net']:,.0f}. Break-even at "
            f"{p['breakeven_customers']:.0f} paying customers. LTV ${p['ltv']:.0f} vs ad CAC ${pa.paid_cac:.0f} "
            f"(LTV/CAC {p['ltv_to_cac']:.2f}) -> paid ads are {'viable' if p['ltv_to_cac'] > 3 else 'NOT viable'} "
            f"at base assumptions; growth must be organic. Support load ~{p['support_hours']:.0f} h/yr unpaid founder time."]
    b, rows = sensitivity(pa, PRODUCT_RANGES, product_year1)
    out += ["", "| input | low | high | net@low | net@high |", "|---|---|---|---|---|"]
    out += [f"| {r[0]} | {r[1]:g} | {r[2]:g} | {r[3]:,.0f} | {r[4]:,.0f} |" for r in rows]
    mc = monte_carlo(pa, PRODUCT_RANGES, product_year1)
    out.append(f"\nMonte Carlo: p05 ${mc['p05']:,.0f}, median ${mc['p50']:,.0f}, p95 ${mc['p95']:,.0f}; "
               f"P(year-1 net>0) = {mc['prob_positive']:.0%}.")
    return "\n".join(out)
