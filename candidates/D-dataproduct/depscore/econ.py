"""Unit-economics model: break-even, sensitivity, and a seeded Monte Carlo.

Every number below is an ASSUMPTION unless its `source` says otherwise. Edit ASSUMPTIONS and re-run
`python -m depscore econ` to regenerate ECONOMICS.md. Money in USD per month unless stated.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np


@dataclass(frozen=True)
class A:
    # --- pricing (our choice) ---
    price_pro: float = 12.0          # individual / small team, monthly
    price_team: float = 49.0         # org: multiple watchlists, custom policy, email support
    team_share: float = 0.15         # fraction of paying accounts on team tier (assumption)
    annual_discount_share: float = 0.0  # ignored in base case (conservative: no prepay cash benefit)
    # --- payment fees ---
    pct_fee: float = 0.05            # Merchant-of-record style (e.g. Lemon Squeezy 5% + 50c, handles VAT/sales tax). Not verified live.
    fixed_fee: float = 0.50          # per transaction
    # --- infrastructure ---
    hosting: float = 0.0             # GitHub Pages + Actions on a PUBLIC repo: free (GitHub docs, from training knowledge; docs site blocked here)
    domain: float = 1.25             # ~$15/yr .dev/.com domain (assumption)
    email_tool: float = 0.0          # alerts delivered via GitHub (private sponsor repo releases/issues) -> $0; swap for an email SaaS if needed
    misc_tools: float = 0.0
    # --- operator time ---
    hours_per_month: float = 6.0     # maintenance, support, false-positive triage (assumption)
    hourly_value: float = 40.0       # opportunity cost of the operator's hour (assumption)
    launch_hours: float = 25.0       # one-time: deploy, landing copy, outreach (assumption, beyond code already built)
    # --- taxes ---
    tax_rate: float = 0.30           # income + self-employment tax on positive profit (assumption; jurisdiction-dependent)
    # --- demand (the big unknowns) ---
    visitors_m1: float = 400.0       # monthly unique visitors in month 1 (assumption)
    visitor_growth: float = 0.10     # monthly growth while cap not reached (assumption)
    visitor_cap: float = 8000.0
    conv: float = 0.003              # visitor -> new paying account per month (assumption; freemium dev-tool range 0.1%-1%)
    churn: float = 0.04              # monthly logo churn (assumption)
    sponsors_per_month: float = 0.0  # pure donations, ignored in base case


BASE = A()

SOURCES = {
    "pct_fee/fixed_fee": "Merchant-of-record pricing published by Lemon Squeezy (5% + 50c) as recalled from training data; Stripe direct is 2.9% + 30c (US cards) but then the operator owns VAT/sales-tax compliance. NOT verified live (sites blocked from this sandbox).",
    "hosting": "GitHub docs (training knowledge): Actions minutes are free for public repositories on standard runners; GitHub Pages sites have a 1 GB size limit and a 100 GB/month soft bandwidth limit. Not re-verified live.",
    "pipeline runtime": "MEASURED here (2026-10-07): 124 mixed PyPI/npm packages in ~4 s and the top-500 PyPI packages in ~25 s wall-clock (8 threads, live registries). Linear extrapolation: 10k packages ~ 8-15 min/day, inside free CI for a public repo. Static output ~9 KB/package (4.5 MB for 500) -> 10k ~ 90 MB, under the 1 GB Pages limit.",
    "demand": "PURE ASSUMPTION. No customer has been asked yet. Treat Monte Carlo output as illustration, not forecast.",
}


def arpa(a: A = BASE) -> float:
    """Average gross revenue per paying account per month."""
    return (1 - a.team_share) * a.price_pro + a.team_share * a.price_team


def net_per_account(a: A = BASE) -> float:
    return arpa(a) * (1 - a.pct_fee) - a.fixed_fee


def fixed_cash(a: A = BASE) -> float:
    return a.hosting + a.domain + a.email_tool + a.misc_tools


def fixed_econ(a: A = BASE) -> float:
    return fixed_cash(a) + a.hours_per_month * a.hourly_value


def break_even_accounts(a: A = BASE, include_time: bool = True) -> int:
    f = fixed_econ(a) if include_time else fixed_cash(a)
    n = net_per_account(a)
    if n <= 0:
        return 10**9
    return int(np.ceil(f / n - 1e-12))


def monthly_profit(accounts: float, a: A = BASE, include_time: bool = True, after_tax: bool = True) -> float:
    pre = accounts * net_per_account(a) - fixed_cash(a)
    if after_tax and pre > 0:
        pre *= 1 - a.tax_rate
    if include_time:
        pre -= a.hours_per_month * a.hourly_value
    return pre


def simulate(a: A = BASE, months: int = 24, rng=None, stochastic: bool = False) -> dict:
    """Subscriber flow model. Returns cumulative cash & economic profit (after tax, incl. launch time)."""
    subs = 0.0
    visitors = a.visitors_m1
    cash = 0.0
    econ = -a.launch_hours * a.hourly_value
    path = []
    for _ in range(months):
        new = visitors * a.conv
        if stochastic:
            new = rng.poisson(new)
            lost = rng.binomial(int(subs), a.churn) if subs > 0 else 0
        else:
            lost = subs * a.churn
        subs = max(0.0, subs + new - lost)
        c = monthly_profit(subs, a, include_time=False)
        cash += c
        econ += c - a.hours_per_month * a.hourly_value
        path.append(subs)
        visitors = min(a.visitor_cap, visitors * (1 + a.visitor_growth))
    return {"subs_end": subs, "cash_24m": cash, "econ_24m": econ, "path": path}


P_NO_FIT = 0.35  # assumption: chance nobody meaningfully pays because free tools (deps.dev, OpenSSF Scorecard) suffice


def monte_carlo(n: int = 5000, seed: int = 7, months: int = 24, p_no_fit: float = P_NO_FIT) -> dict:
    rng = np.random.default_rng(seed)
    res = []
    for _ in range(n):
        no_fit = rng.random() < p_no_fit
        a = replace(
            BASE,
            visitors_m1=float(rng.lognormal(np.log(300), 0.8)),
            visitor_growth=float(rng.uniform(0.0, 0.15)),
            conv=float(rng.lognormal(np.log(0.002), 0.8)),
            churn=float(rng.uniform(0.02, 0.08)),
            hours_per_month=float(rng.uniform(3, 12)),
            team_share=float(rng.uniform(0.05, 0.3)),
        )
        if no_fit:
            a = replace(a, conv=a.conv * 0.05)
        r = simulate(a, months, rng=rng, stochastic=True)
        res.append((r["cash_24m"], r["econ_24m"], r["subs_end"]))
    arr = np.array(res)
    pct = lambda col, q: float(np.percentile(arr[:, col], q))
    return {
        "n": n, "seed": seed,
        "p_cash_positive": float((arr[:, 0] > 0).mean()),
        "p_econ_positive": float((arr[:, 1] > 0).mean()),
        "cash_p10": pct(0, 10), "cash_p50": pct(0, 50), "cash_p90": pct(0, 90), "cash_mean": float(arr[:, 0].mean()),
        "econ_p10": pct(1, 10), "econ_p50": pct(1, 50), "econ_p90": pct(1, 90), "econ_mean": float(arr[:, 1].mean()),
        "subs_p10": pct(2, 10), "subs_p50": pct(2, 50), "subs_p90": pct(2, 90),
    }


def sensitivity_grid(prices=(6, 9, 12, 19, 29), hours=(2, 4, 6, 10, 15)) -> list[list]:
    rows = []
    for h in hours:
        rows.append([h] + [break_even_accounts(replace(BASE, price_pro=p, hours_per_month=h)) for p in prices])
    return rows


def tornado(months: int = 24) -> list[tuple[str, float, float]]:
    base = simulate(BASE, months)["econ_24m"]
    knobs = {
        "conv (0.1% .. 0.6%)": ("conv", 0.001, 0.006),
        "visitors_m1 (150 .. 1500)": ("visitors_m1", 150, 1500),
        "visitor_growth (0% .. 20%)": ("visitor_growth", 0.0, 0.2),
        "churn (8% .. 2%)": ("churn", 0.08, 0.02),
        "hours_per_month (12 .. 3)": ("hours_per_month", 12, 3),
        "price_pro ($6 .. $19)": ("price_pro", 6, 19),
        "pct_fee (8% .. 3%)": ("pct_fee", 0.08, 0.03),
    }
    out = []
    for label, (k, lo, hi) in knobs.items():
        out.append((label, simulate(replace(BASE, **{k: lo}), months)["econ_24m"] - base,
                    simulate(replace(BASE, **{k: hi}), months)["econ_24m"] - base))
    return sorted(out, key=lambda t: -abs(t[2] - t[1]))


def write_report(path: str = "ECONOMICS.md") -> str:
    a = BASE
    det = simulate(a)
    mc = monte_carlo()
    grid = sensitivity_grid()
    L = ["# Unit economics (generated by `python -m depscore econ`)", "",
         "All inputs are assumptions unless marked MEASURED. Edit `depscore/econ.py` and regenerate.", "",
         "## Base-case inputs", "", "| parameter | value |", "|---|---|"]
    for k, v in a.__dict__.items():
        L.append(f"| {k} | {v} |")
    L += ["", "## Sources / provenance", ""] + [f"- **{k}**: {v}" for k, v in SOURCES.items()]
    L += ["", "## Per-account economics", "",
          f"- ARPA (blended gross / paying account / month): **${arpa(a):.2f}**",
          f"- Net after payment fees: **${net_per_account(a):.2f}**",
          f"- Fixed cash cost / month (hosting+domain+tools): **${fixed_cash(a):.2f}**",
          f"- Fixed economic cost / month incl. operator time ({a.hours_per_month} h x ${a.hourly_value}): **${fixed_econ(a):.2f}**",
          f"- **Cash break-even: {break_even_accounts(a, False)} paying account(s)**",
          f"- **Break-even including operator time: {break_even_accounts(a, True)} paying accounts**",
          f"- One-time launch time valued at ${a.launch_hours * a.hourly_value:.0f} ({a.launch_hours} h).", "",
          "## Sensitivity: accounts needed to break even incl. operator time", "",
          "| hours/month \\ Pro price | $6 | $9 | $12 | $19 | $29 |", "|---|---|---|---|---|---|"]
    for r in grid:
        L.append("| " + " | ".join(str(x) for x in r) + " |")
    L += ["", "## Monthly profit at N paying accounts (base prices, after 30% tax on positive profit)", "",
          "| accounts | cash profit/mo | economic profit/mo (minus operator time) |", "|---|---|---|"]
    for n in (0, 1, 5, 10, 25, 50, 100, 250):
        L.append(f"| {n} | ${monthly_profit(n, a, False):.0f} | ${monthly_profit(n, a, True):.0f} |")
    L += ["", "## Deterministic base-case 24-month path", "",
          f"- Paying accounts at month 6/12/24: {det['path'][5]:.1f} / {det['path'][11]:.1f} / {det['path'][23]:.1f}",
          f"- Cumulative after-tax cash profit (24 m): **${det['cash_24m']:.0f}**",
          f"- Cumulative economic profit incl. operator + launch time (24 m): **${det['econ_24m']:.0f}**", "",
          "## Tornado: change in 24-month economic profit vs base (low, high)", "", "| driver | low | high |", "|---|---|---|"]
    for lab, lo, hi in tornado():
        L.append(f"| {lab} | {lo:+.0f} | {hi:+.0f} |")
    L += ["", f"## Monte Carlo ({mc['n']} runs, seed {mc['seed']}, 24 months, wide priors on demand)", "",
          f"Priors: visitors_m1 ~ lognormal(median 300, sigma 0.8); growth ~ U(0, 15%); conversion ~ lognormal(median 0.2%, sigma 0.8); churn ~ U(2%, 8%); hours ~ U(3, 12); team share ~ U(5%, 30%). With probability {P_NO_FIT:.0%} the run is a 'no product-market fit' world where conversion is cut 20x.", "",
          "| metric | P10 | P50 | P90 | mean |", "|---|---|---|---|---|",
          f"| paying accounts at m24 | {mc['subs_p10']:.0f} | {mc['subs_p50']:.0f} | {mc['subs_p90']:.0f} | |",
          f"| cumulative cash profit | ${mc['cash_p10']:.0f} | ${mc['cash_p50']:.0f} | ${mc['cash_p90']:.0f} | ${mc['cash_mean']:.0f} |",
          f"| cumulative economic profit | ${mc['econ_p10']:.0f} | ${mc['econ_p50']:.0f} | ${mc['econ_p90']:.0f} | ${mc['econ_mean']:.0f} |", "",
          f"- P(cash profit > 0 over 24 m) = **{mc['p_cash_positive']:.0%}**",
          f"- P(economic profit > 0, i.e. beats valuing operator time at ${a.hourly_value}/h) = **{mc['p_econ_positive']:.0%}**", ""]
    text = "\n".join(L)
    with open(path, "w") as f:
        f.write(text)
    return text
