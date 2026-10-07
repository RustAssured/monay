"""Render an AuditResult as Markdown or JSON."""
from __future__ import annotations

import json


def to_markdown(res, top=15) -> str:
    L = []
    start, end = res.transactions[0].date, res.transactions[-1].date
    L.append(f"# Spending audit ({start} to {end}, {len(res.transactions)} transactions)\n")
    L.append("Everything below is computed locally from your exports. Estimates are not guarantees; "
             "'expected' applies an assumed probability that you can and will act on each item.\n")
    L.append(f"**Identified:** ${res.identified_annual:,.2f}/yr-equivalent  |  "
             f"**Confidence-weighted expected:** ${res.expected_annual:,.2f} over the next 12 months\n")
    L.append("## Ranked actions\n")
    L.append("| # | action | annual | one-time | confidence | expected 12m | effort |")
    L.append("|---|---|---|---|---|---|---|")
    for i, a in enumerate(res.actions[:top], 1):
        L.append(f"| {i} | {a.title} | ${a.annual_value:,.2f} | ${a.one_time_value:,.2f} | {a.confidence:.0%} | "
                 f"${a.expected_value_12m:,.2f} | {a.effort_minutes} min |")
    L.append("")
    for i, a in enumerate(res.actions[:top], 1):
        L.append(f"{i}. **{a.title}** — {a.detail}")
    L.append("\n## Recurring commitments (active)\n")
    L.append("| merchant | cadence | kind | typical | per month | per year | since |")
    L.append("|---|---|---|---|---|---|---|")
    act = [s for s in res.recurring if s.direction == "out" and s.active]
    for s in act:
        L.append(f"| {s.merchant} | {s.cadence} | {s.kind} | ${s.typical_amount:,.2f} | ${s.monthly_cost:,.2f} | "
                 f"${s.annual_cost:,.2f} | {s.transactions[0].date} |")
    L.append(f"\nTotal active recurring outflow: ${sum(s.monthly_cost for s in act):,.2f}/month.")
    gone = [s for s in res.recurring if s.direction == "out" and not s.active]
    if gone:
        L.append("Stopped (no recent charge): " + ", ".join(f"{s.merchant} (last {s.transactions[-1].date})"
                                                           for s in gone))
    L.append("\n## Monthly net cash flow (history, transfers between own accounts excluded)\n")
    L.append("| month | in | out | net |\n|---|---|---|---|")
    for m, i, o, n in res.history:
        L.append(f"| {m} | ${i:,.2f} | ${o:,.2f} | ${n:,.2f} |")
    L.append("\n## Forecast\n")
    L.append("Recurring items projected on their cadence; variable spending/income = median of recent full months.\n")
    L.append("| month | recurring in | other in | committed out | variable out | net | net after actions |")
    L.append("|---|---|---|---|---|---|---|")
    for f in res.forecast:
        L.append(f"| {f['month']} | ${f['recurring_income']:,.2f} | ${f['other_income']:,.2f} | "
                 f"${f['committed_out']:,.2f} | ${f['variable_out']:,.2f} | ${f['net']:,.2f} | "
                 f"${f['net_after_actions']:,.2f} |")
    return "\n".join(L) + "\n"


def to_json(res) -> str:
    return json.dumps({
        "identified_annual": round(res.identified_annual, 2),
        "expected_annual": round(res.expected_annual, 2),
        "actions": [{"kind": a.kind, "title": a.title, "detail": a.detail, "annual_value": round(a.annual_value, 2),
                     "one_time_value": round(a.one_time_value, 2), "confidence": a.confidence,
                     "expected_value_12m": round(a.expected_value_12m, 2), "effort_minutes": a.effort_minutes,
                     "evidence_txids": a.evidence} for a in res.actions],
        "recurring": [{"merchant": s.merchant, "cadence": s.cadence, "kind": s.kind, "direction": s.direction,
                       "active": s.active, "typical_amount": round(s.typical_amount, 2),
                       "annual": round(s.annual_cost, 2), "count": len(s.transactions)} for s in res.recurring],
        "history": [{"month": m, "in": round(i, 2), "out": round(o, 2), "net": round(n, 2)}
                    for m, i, o, n in res.history],
        "forecast": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in f.items()} for f in res.forecast],
    }, indent=2)
