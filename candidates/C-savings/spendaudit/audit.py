"""Glue: run all detectors, build a ranked, dollar-quantified action list and a cash-flow forecast."""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import detectors as D
from .normalize import is_transfer

FEE_ADVICE = {
    "overdraft": ("Stop overdraft fees", "Opt out of overdraft coverage (declined instead of $35 fee), set a low-balance "
                  "alert, or move to an account with no overdraft fees.", 0.8, 20),
    "maintenance": ("Eliminate monthly account fee", "Meet the waiver condition (direct deposit / min balance) or "
                    "switch to a no-monthly-fee account.", 0.85, 45),
    "atm": ("Avoid out-of-network ATM fees", "Use in-network ATMs or an account that reimburses ATM fees; get "
            "cash back at checkout.", 0.7, 10),
    "fx": ("Avoid foreign transaction fees", "Use a card with no foreign transaction fee for travel/foreign "
           "merchants.", 0.75, 30),
    "interest": ("Stop paying card interest", "Pay statement balance in full / set autopay to full balance; if "
                 "carrying debt, consider a lower-rate option.", 0.5, 20),
    "late": ("Avoid late fees", "Turn on autopay for at least the minimum payment.", 0.9, 5),
    "wire": ("Reduce wire fees", "Use ACH or free transfer options when timing allows.", 0.6, 10),
}


@dataclass
class Action:
    kind: str
    title: str
    detail: str
    annual_value: float      # recurring yearly saving or gain
    one_time_value: float    # e.g. a refund
    confidence: float        # probability the user can/will realise it (assumed, see REPORT.md)
    effort_minutes: int
    evidence: list = field(default_factory=list)

    @property
    def expected_value_12m(self):
        return (self.annual_value + self.one_time_value) * self.confidence


@dataclass
class AuditResult:
    transactions: list
    recurring: list
    price_creep: list
    duplicates: list
    fees: list
    reviews: list
    idle: list
    actions: list
    history: list
    forecast: list

    @property
    def identified_annual(self):
        return sum(a.annual_value + a.one_time_value for a in self.actions)

    @property
    def expected_annual(self):
        return sum(a.expected_value_12m for a in self.actions)


def _month(d):
    return (d.year, d.month)


def monthly_history(transactions):
    rows = defaultdict(lambda: [0.0, 0.0])
    for t in transactions:
        if is_transfer(t.description):
            continue
        rows[_month(t.date)][0 if t.amount > 0 else 1] += abs(t.amount)
    return [(f"{y}-{m:02d}", inc, out, inc - out) for (y, m), (inc, out) in sorted(rows.items())]


def forecast(transactions, recurring, actions, months=6):
    end = D.data_end(transactions)
    start = min(t.date for t in transactions)
    rec_ids = set()
    for s in recurring:
        rec_ids |= s.txids
    # baseline variable (non-recurring, non-transfer) spend & income over complete months
    var_out, var_in = defaultdict(float), defaultdict(float)
    for t in transactions:
        if t.txid in rec_ids or is_transfer(t.description):
            continue
        (var_in if t.amount > 0 else var_out)[_month(t.date)] += abs(t.amount)
    full = [m for m in {_month(t.date) for t in transactions}
            if m != _month(start) and m != _month(end)] or list({_month(t.date) for t in transactions})
    full = sorted(full)[-6:]
    base_out = statistics.median([var_out[m] for m in full]) if full else 0.0
    base_in = statistics.median([var_in[m] for m in full]) if full else 0.0
    monthly_saving = sum(a.annual_value * a.confidence for a in actions) / 12

    y, mo = end.year, end.month
    out = []
    for _ in range(months):
        mo += 1
        if mo == 13:
            y, mo = y + 1, 1
        committed_in = committed_out = 0.0
        for s in recurring:
            if not s.active:
                continue
            step = timedelta(days=D.CADENCES[s.cadence][0])
            d = s.transactions[-1].date + step
            while (d.year, d.month) <= (y, mo):
                if (d.year, d.month) == (y, mo):
                    if s.direction == "in":
                        committed_in += s.typical_amount
                    else:
                        committed_out += s.typical_amount
                d += step
        net = committed_in + base_in - committed_out - base_out
        out.append({"month": f"{y}-{mo:02d}", "recurring_income": committed_in, "other_income": base_in,
                    "committed_out": committed_out, "variable_out": base_out, "net": net,
                    "net_after_actions": net + monthly_saving})
    return out


def run_audit(transactions, usage=None, checking_accounts=None, balances=None, target_apy=0.035,
              current_apy=0.0001, buffer_months=1.5, forecast_months=6):
    txs = sorted(transactions, key=lambda t: t.date)
    rec = D.detect_recurring(txs)
    rec_out = [s for s in rec if s.direction == "out"]
    creep = D.detect_price_creep(rec_out)
    dups = D.detect_duplicates(txs, rec)
    fees = D.detect_fees(txs)
    reviews = D.detect_overlap_and_unused(rec_out, usage=usage)
    accounts = checking_accounts or sorted({t.account for t in txs if any(
        x.balance is not None for x in txs if x.account == t.account)} | set((balances or {}).keys()))
    idle = []
    for acct in accounts:
        ic = D.detect_idle_cash(txs, acct, closing=(balances or {}).get(acct), buffer_months=buffer_months,
                                current_apy=current_apy, target_apy=target_apy)
        if ic:
            idle.append(ic)

    actions = []
    for r in reviews:
        actions.append(Action("subscription", "Review/cancel: " + ", ".join(s.merchant for s in r.series[:1]) +
                              ("" if len(r.series) == 1 else " (overlap)"), r.reason, r.potential_annual, 0.0,
                              r.confidence, 10, [t.txid for s in r.series for t in s.transactions[-3:]]))
    under_review = {id(s) for r in reviews for s in r.series if r.confidence >= 0.7}
    for p in creep:
        if id(p.series) in under_review:
            continue  # already suggested cancelling it; counting the increase too would double-count
        actions.append(Action("price_creep", f"Price increase: {p.series.merchant} "
                              f"${p.old_amount:.2f} -> ${p.new_amount:.2f} ({p.pct:+.0%}) since {p.changed_on}",
                              "Ask for the old rate / a retention offer, downgrade the plan, or cancel.",
                              p.annual_increase, 0.0, 0.3, 15, [p.series.transactions[-1].txid]))
    for d in dups:
        actions.append(Action("duplicate", f"Possible duplicate charge: {d.duplicate.description} "
                              f"${d.amount:.2f} on {d.original.date} and {d.duplicate.date}",
                              "Confirm with the merchant; if not intended, request a refund or dispute with the "
                              "card issuer (US: Fair Credit Billing Act, generally within 60 days of the statement).",
                              0.0, d.amount, d.confidence, 15, [d.original.txid, d.duplicate.txid]))
    for f in fees:
        title, detail, conf, effort = FEE_ADVICE[f.kind]
        actions.append(Action("fee", f"{title}: ${f.total:.2f} paid over {f.span_days} days "
                              f"({len(f.transactions)} charge{'s' if len(f.transactions) != 1 else ''})", detail, f.annualized, 0.0, conf, effort,
                              [t.txid for t in f.transactions]))
    for ic in idle:
        actions.append(Action("idle_cash", f"Idle cash in {ic.account}: ~${ic.excess:,.0f} above a "
                              f"{buffer_months}-month buffer", f"Move ~${ic.excess:,.0f} to an FDIC/NCUA-insured "
                              f"savings account. Gain assumes {ic.target_apy:.2%} vs {ic.current_apy:.2%} APY; rates "
                              "are variable — check current rates.", ic.annual_gain, 0.0, 0.9, 30, []))
    actions.sort(key=lambda a: (-a.expected_value_12m, a.effort_minutes))
    return AuditResult(txs, rec, creep, dups, fees, reviews, idle, actions, monthly_history(txs),
                       forecast(txs, rec, actions, forecast_months))
