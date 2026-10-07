"""Detectors: recurring charges, price creep, duplicate charges, fees, overlapping/unused services, idle cash.

All detectors are deterministic, explainable heuristics (no ML) so every flag carries its evidence.
"""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .normalize import AMBIGUOUS_BILLERS, CLOUD_STORAGE, MUSIC, STREAMING, fee_type, is_transfer, merchant_family, merchant_key

# cadence name -> (nominal days, per-interval tolerance in days, periods per year)
CADENCES = {
    "weekly": (7, 1.5, 52.18),
    "biweekly": (14, 2.5, 26.09),
    "semimonthly": (15.2, 3.5, 24),
    "monthly": (30.44, 4.5, 12),
    "quarterly": (91.3, 10, 4),
    "semiannual": (182.6, 15, 2),
    "annual": (365.25, 20, 1),
}
MIN_OCCURRENCES = {"weekly": 4, "biweekly": 3, "semimonthly": 4, "monthly": 3, "quarterly": 3,
                   "semiannual": 2, "annual": 2}


@dataclass
class RecurringSeries:
    merchant: str
    cadence: str
    transactions: list
    direction: str  # "out" or "in"
    amount_cv: float
    active: bool = True
    kind: str = "fixed"  # fixed (same price each time) | variable (e.g. utility bill) | income

    @property
    def amounts(self):
        return [abs(t.amount) for t in self.transactions]

    @property
    def last_amount(self):
        return abs(self.transactions[-1].amount)

    @property
    def periods_per_year(self):
        return CADENCES[self.cadence][2]

    @property
    def typical_amount(self):
        a = self.amounts
        return a[-1] if self.kind == "fixed" else statistics.median(a[-6:])

    @property
    def annual_cost(self):
        return self.typical_amount * self.periods_per_year

    @property
    def monthly_cost(self):
        return self.annual_cost / 12

    @property
    def txids(self):
        return {t.txid for t in self.transactions}


def _cv(xs):
    if len(xs) < 2:
        return 0.0
    m = statistics.mean(xs)
    return statistics.pstdev(xs) / m if m else 0.0


def _match_cadence(dates):
    if len(dates) < 2:
        return None
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    best = None
    for name, (nominal, tol, _) in CADENCES.items():
        if len(dates) < MIN_OCCURRENCES[name]:
            continue
        ok = sum(1 for g in gaps if abs(g - nominal) <= tol)
        # allow one skipped period (e.g. a missed month) counting as a gap of ~2x nominal
        ok += sum(1 for g in gaps if abs(g - 2 * nominal) <= tol * 1.5) * 0.5
        frac = ok / len(gaps)
        if frac >= 0.8 and (best is None or frac > best[1]):
            best = (name, frac)
    return best[0] if best else None


def _amount_clusters(txs):
    """Split one merchant's charges into clusters of similar amount (e.g. Apple iCloud vs Apple Music)."""
    srt = sorted(txs, key=lambda t: abs(t.amount))
    clusters, cur = [], [srt[0]]
    for t in srt[1:]:
        if abs(t.amount) > abs(cur[-1].amount) * 1.25 + 0.5:
            clusters.append(cur)
            cur = [t]
        else:
            cur.append(t)
    clusters.append(cur)
    return [sorted(c, key=lambda t: t.date) for c in clusters]


def _series_from(txs, merchant, direction, end, split=False, others=0):
    dates = [t.date for t in txs]
    cad = _match_cadence(dates)
    if not cad:
        return None
    amounts = [abs(t.amount) for t in txs]
    cv = _cv(amounts)
    if direction == "out":
        if cv > 0.30:          # too variable to be a commitment (groceries, gas)
            return None
        # fixed-price subscriptions: amounts take only a few distinct values
        distinct = len({round(a, 2) for a in amounts})
        if split and distinct > 2:
            return None   # a sub-cluster of a merchant with other spending must look like a fixed price
        if len(amounts) == 2 and (others > 0 or distinct > 1 and abs(amounts[1] - amounts[0]) > 0.25 * amounts[0]):
            return None   # two charges a year apart are only credible if nothing else is bought there
        kind = "fixed" if distinct <= max(2, len(amounts) // 4) else "variable"
    else:
        if cv > 0.35:
            return None
        kind = "income"
    nominal = CADENCES[cad][0]
    active = (end - dates[-1]).days <= nominal * 1.5 + 5
    keys = [merchant_key(t.description) for t in txs]
    display = max(set(keys), key=lambda k: (keys.count(k), -len(k)))  # most common descriptor form
    return RecurringSeries(display, cad, txs, direction, cv, active, kind)


def data_end(transactions):
    return max(t.date for t in transactions)


def detect_recurring(transactions, include_income=True):
    end = data_end(transactions)
    groups = defaultdict(list)
    for t in transactions:
        if is_transfer(t.description) or fee_type(t.description, t.amount):
            continue
        direction = "out" if t.amount < 0 else "in"
        if direction == "in" and not include_income:
            continue
        groups[(merchant_family(t.description), direction)].append(t)
    found = []
    for (m, direction), txs in groups.items():
        txs.sort(key=lambda t: t.date)
        if len(txs) < 2:
            continue
        s = _series_from(txs, m, direction, end)
        if s:
            found.append(s)
            continue
        for c in _amount_clusters(txs):
            if len(c) >= 2:
                s = _series_from(c, m, direction, end, split=True, others=len(txs) - len(c))
                if s:
                    found.append(s)
    found.sort(key=lambda s: -s.annual_cost)
    return found


# ---------------------------------------------------------------- price creep
@dataclass
class PriceIncrease:
    series: RecurringSeries
    old_amount: float
    new_amount: float
    changed_on: date

    @property
    def pct(self):
        return (self.new_amount - self.old_amount) / self.old_amount

    @property
    def annual_increase(self):
        return (self.new_amount - self.old_amount) * self.series.periods_per_year


def detect_price_creep(series_list, min_pct=0.02, min_abs=0.25):
    out = []
    for s in series_list:
        if s.direction != "out" or s.kind != "fixed" or not s.active or len(s.transactions) < 3:
            continue
        a = s.amounts
        # the most recent sustained step up: amount rose and every later charge is at least the new level
        for i in range(len(a) - 1, 0, -1):
            if a[i] - a[i - 1] >= max(min_abs, a[i - 1] * min_pct) and min(a[i:]) >= a[i] - 0.01:
                baseline = statistics.median(a[max(0, i - 3):i])
                out.append(PriceIncrease(s, baseline, a[-1], s.transactions[i].date))
                break
    return out


# ---------------------------------------------------------------- duplicates
@dataclass
class DuplicateCharge:
    original: object
    duplicate: object
    confidence: float

    @property
    def amount(self):
        return abs(self.duplicate.amount)


def detect_duplicates(transactions, recurring=None, max_days=1, min_amount=10.0):
    recurring_ids = set()
    for s in recurring or []:
        if s.cadence in ("weekly",):
            recurring_ids |= s.txids
    by_key = defaultdict(list)
    refunds = defaultdict(list)
    freq = defaultdict(int)
    for t in transactions:
        m = merchant_key(t.description)
        if t.amount < 0:
            freq[m] += 1
            by_key[(t.account, m, round(t.amount, 2))].append(t)
        elif t.amount > 0:
            refunds[(m, round(t.amount, 2))].append(t)
    span_months = max(1.0, (data_end(transactions) - min(t.date for t in transactions)).days / 30.44)
    out = []
    for (acct, m, amt), txs in by_key.items():
        if -amt < min_amount or is_transfer(txs[0].description) or fee_type(txs[0].description, amt):
            continue
        txs.sort(key=lambda t: t.date)
        used = set()
        for a, b in zip(txs, txs[1:]):
            if a.txid in used or (b.date - a.date).days > max_days:
                continue
            if a.txid in recurring_ids and b.txid in recurring_ids:
                continue
            refunded = any(0 <= (r.date - b.date).days <= 45 for r in refunds.get((m, -amt), []))
            if refunded:
                continue
            visits_per_month = freq[m] / span_months
            conf = 0.85 if (b.date - a.date).days == 0 else 0.7
            if visits_per_month > 4:
                conf -= 0.35   # habitual merchant: repeat purchases of the same amount are common
            if -amt >= 50:
                conf += 0.05
            out.append(DuplicateCharge(a, b, round(min(conf, 0.95), 2)))
            used.add(b.txid)
    out.sort(key=lambda d: -d.amount * d.confidence)
    return out


# ---------------------------------------------------------------- fees
@dataclass
class FeeSummary:
    kind: str
    transactions: list
    span_days: int

    @property
    def total(self):
        return sum(-t.amount for t in self.transactions)

    @property
    def annualized(self):
        # With < 90 days of history, do not extrapolate: one trip's FX fees are not a yearly habit.
        if self.span_days < 90:
            return self.total
        return self.total * 365.25 / self.span_days


def detect_fees(transactions):
    """Fees grouped by type. The annualisation window is the history of the accounts the fees were charged on."""
    acct_span = {}
    for t in transactions:
        lo, hi = acct_span.get(t.account, (t.date, t.date))
        acct_span[t.account] = (min(lo, t.date), max(hi, t.date))
    groups = defaultdict(list)
    for t in transactions:
        k = fee_type(t.description, t.amount)
        if k:
            groups[k].append(t)
    out = []
    for k, v in groups.items():
        accts = {t.account for t in v}
        span = max((acct_span[a][1] - acct_span[a][0]).days + 1 for a in accts)
        out.append(FeeSummary(k, v, span))
    return sorted(out, key=lambda f: -f.annualized)


# ---------------------------------------------------------------- overlap / unused
@dataclass
class ServiceReview:
    reason: str
    series: list
    potential_annual: float
    confidence: float


def detect_overlap_and_unused(series_list, usage=None, as_of=None, stale_days=60):
    """Two signals, both requiring the user to confirm:
    1. Overlap: >=2 active services in the same category (e.g. 3 video-streaming services). The cheapest
       candidate to drop is quantified, not all of them.
    2. Unused: the user supplies a usage log {merchant_key: last_used_date}; services idle > stale_days.
    Bank data alone cannot prove a service is unused, so this never claims that without a usage log."""
    out = []
    active = [s for s in series_list if s.active and s.direction == "out"]
    used = set()
    for cat_name, cat in (("video streaming", STREAMING), ("music", MUSIC), ("cloud storage", CLOUD_STORAGE)):
        # one series per merchant, each series in at most one category, ambiguous billers excluded
        members, seen = [], set()
        for s in sorted(active, key=lambda s: s.annual_cost):
            if s.merchant in cat and s.merchant not in AMBIGUOUS_BILLERS and s.merchant not in seen \
                    and id(s) not in used and s.kind == "fixed" and s.typical_amount < 40:
                members.append(s)
                seen.add(s.merchant)
        if len(members) >= (3 if cat_name == "video streaming" else 2):
            used |= {id(s) for s in members}
            drop = members[0]
            out.append(ServiceReview(
                f"{len(members)} overlapping {cat_name} services ({', '.join(s.merchant for s in members)}); "
                f"dropping one (e.g. {drop.merchant}) or rotating month-to-month",
                members, drop.annual_cost, 0.4))
    if usage:
        as_of = as_of or max(s.transactions[-1].date for s in active)
        for s in active:
            last = usage.get(s.merchant)
            if last is not None and (as_of - last).days > stale_days:
                out.append(ServiceReview(f"{s.merchant} not used for {(as_of - last).days} days (per your usage log)",
                                         [s], s.annual_cost, 0.75))
    return out


# ---------------------------------------------------------------- idle cash
@dataclass
class IdleCash:
    account: str
    p10_balance: float
    avg_balance: float
    monthly_outflow: float
    buffer: float
    excess: float
    current_apy: float
    target_apy: float

    @property
    def annual_gain(self):
        return max(0.0, self.excess) * (self.target_apy - self.current_apy)


def daily_balances(transactions, account, opening=None):
    """Reconstruct end-of-day balances. Uses bank-provided running balances when present,
    otherwise needs an opening balance (or a closing ledger balance worked backwards)."""
    txs = sorted([t for t in transactions if t.account == account], key=lambda t: t.date)
    if not txs:
        return {}
    eod = {}
    if all(t.balance is not None for t in txs):
        for t in txs:
            eod[t.date] = t.balance  # last one in the day wins (rows are in posting order)
    elif opening is not None:
        bal = opening
        for t in txs:
            bal += t.amount
            eod[t.date] = bal
    else:
        return {}
    days, out, cur = sorted(eod), {}, None
    d = days[0]
    while d <= days[-1]:
        cur = eod.get(d, cur)
        out[d] = cur
        d += timedelta(days=1)
    return out


def detect_idle_cash(transactions, account, opening=None, closing=None, buffer_months=1.5,
                     current_apy=0.0001, target_apy=0.035, min_excess=2500.0):
    txs = [t for t in transactions if t.account == account]
    if not txs:
        return None
    if opening is None and closing is not None and not all(t.balance is not None for t in txs):
        opening = closing[1] - sum(t.amount for t in txs)
    bals = daily_balances(transactions, account, opening)
    if not bals:
        return None
    vals = sorted(bals.values())
    p10 = vals[int(len(vals) * 0.10)]
    months = max(1.0, len(vals) / 30.44)
    spend_out = sum(-t.amount for t in txs if t.amount < 0 and not is_transfer(t.description))
    # card bill payments are spending paid via checking; transfers to savings/investments are not
    card_pay = sum(-t.amount for t in txs if t.amount < 0 and is_transfer(t.description)
                   and re.search(r"\b(CARD|CRD|AUTOPAY|CREDIT)\b", t.description.upper()))
    monthly_out = (spend_out + card_pay) / months
    buffer = buffer_months * monthly_out
    excess = p10 - buffer
    ic = IdleCash(account, p10, statistics.mean(vals), monthly_out, buffer, excess, current_apy, target_apy)
    return ic if excess >= min_excess else None
