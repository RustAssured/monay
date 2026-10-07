"""Broker-agnostic order generation and (simulated) execution."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from .config import CostConfig, Instrument, StrategyConfig
from .portfolio import Portfolio, Realized, TaxLedger


@dataclass
class Order:
    ticker: str
    side: str          # "buy" | "sell"
    qty: float
    est_price: float
    reason: str = ""

    @property
    def notional(self) -> float:
        return self.qty * self.est_price


@dataclass
class Fill:
    order: Order
    price: float
    fee: float
    cost: float                # spread + slippage paid vs mid, in $
    realized: list[Realized]


def effective_targets(strategy: StrategyConfig, risk_off: bool = False) -> dict[str, float]:
    t = dict(strategy.targets)
    if risk_off and t.get("stock", 0) > 0:
        t["bill"] = t.get("bill", 0.0) + t["stock"]
        t["stock"] = 0.0
    return t


def drift_triggered(weights: dict[str, float], targets: dict[str, float],
                    band_abs: float, band_rel: float) -> bool:
    for a, tw in targets.items():
        d = abs(weights.get(a, 0.0) - tw)
        if d > band_abs + 1e-12:
            return True
        if tw > 0 and d > band_rel * tw + 1e-12:
            return True
    return False


def choose_buy_ticker(p: Portfolio, strategy: StrategyConfig, asset: str, today: date) -> str | None:
    for t in strategy.tickers[asset]:
        if not p.buy_blocked(t, today):
            return t
    return None


def _cost_frac(inst: Instrument, cost: CostConfig) -> float:
    return (inst.half_spread_bps * cost.spread_multiplier + cost.slippage_bps) / 1e4


def plan_orders(p: Portfolio, prices: dict[str, float], strategy: StrategyConfig,
                instruments: dict[str, Instrument], cost: CostConfig, today: date,
                risk_off: bool = False, force: bool = False) -> list[Order]:
    """Generate orders that move the portfolio toward target weights.

    * If drift exceeds the 5/25-style bands (or ``force``): full rebalance.
    * Otherwise: only invest spare cash into the most under-weight assets
      (cash-flow rebalancing, avoids realizing gains).
    * If cash is negative (e.g. a tax bill), raise cash from over-weight assets.
    """
    asset_of = {t: i.asset for t, i in instruments.items()}
    targets = effective_targets(strategy, risk_off)
    total = p.value(prices)
    if total <= 0:
        return []
    cur = p.asset_values(prices, asset_of)
    for a in targets:
        cur.setdefault(a, 0.0)
    invested = total * (1 - strategy.cash_buffer)
    weights = {a: v / total for a, v in cur.items()}
    triggered = force or drift_triggered(weights, targets, strategy.band_abs, strategy.band_rel)
    # assets held but no longer targeted (weight 0) also count via targets.get(a, 0)
    delta: dict[str, float] = {}
    if triggered:
        for a in set(cur) | set(targets):
            delta[a] = targets.get(a, 0.0) * invested - cur.get(a, 0.0)
        reason = "rebalance"
    else:
        spare = p.cash - strategy.cash_buffer * total
        if spare >= 0:
            if spare < strategy.min_trade_value:
                return []
            deficits = {a: max(targets[a] * invested - cur[a], 0.0) for a in targets}
            dsum = sum(deficits.values())
            fill = min(spare, dsum)
            for a in targets:
                delta[a] = fill * deficits[a] / dsum if dsum > 0 else 0.0
            left = spare - fill
            for a in targets:
                delta[a] += left * targets[a]
            reason = "cash-sweep"
        else:
            need = -spare * 1.002 + 1.0  # small margin for trading costs
            over = {a: max(cur[a] - targets.get(a, 0) * invested, 0.0) for a in cur}
            osum = sum(over.values())
            base = over if osum >= need else {a: cur[a] for a in cur}
            bsum = sum(base.values()) or 1.0
            for a in cur:
                delta[a] = -need * base[a] / bsum
            reason = "raise-cash"

    orders: list[Order] = []
    # sells: across tickers of the asset, sell lots with lowest gain% first
    for a, d in delta.items():
        if d >= -strategy.min_trade_value and not (reason == "raise-cash" and d < 0):
            continue
        amount = -d
        lots = [l for t in p.tickers() if asset_of[t] == a for l in p.lots[t]]
        lots.sort(key=lambda l: prices[l.ticker] * l.qty / l.basis if l.basis > 0 else math.inf)
        per_ticker: dict[str, float] = {}
        for l in lots:
            if amount <= 1e-9:
                break
            px = prices[l.ticker]
            q = min(l.qty, amount / px)
            per_ticker[l.ticker] = per_ticker.get(l.ticker, 0.0) + q
            amount -= q * px
        for t, q in per_ticker.items():
            if not strategy.fractional:
                q = math.ceil(q - 1e-9) if q < p.qty(t) else q
                q = min(q, p.qty(t))
            if q > 1e-12:
                orders.append(Order(t, "sell", q, prices[t], reason))
    # buys: budget = cash after sells, net of costs
    budget = p.cash - strategy.cash_buffer * total
    for o in orders:
        budget += o.notional * (1 - _cost_frac(instruments[o.ticker], cost)) - cost.commission_per_order
    buys = {a: d for a, d in delta.items() if d > strategy.min_trade_value}
    bsum = sum(buys.values())
    scale = min(1.0, max(budget, 0.0) / bsum) if bsum > 0 else 0.0
    for a, d in sorted(buys.items(), key=lambda kv: -kv[1]):
        t = choose_buy_ticker(p, strategy, a, today)
        if t is None:
            continue
        cf = _cost_frac(instruments[t], cost)
        amt = d * scale - cost.commission_per_order
        q = amt / (prices[t] * (1 + cf))
        if not strategy.fractional:
            q = math.floor(q)
        if q * prices[t] >= strategy.min_trade_value:
            orders.append(Order(t, "buy", q, prices[t], reason))
    return orders


def execute(p: Portfolio, orders: list[Order], prices: dict[str, float],
            instruments: dict[str, Instrument], cost: CostConfig, today: date,
            ledger: TaxLedger | None = None) -> list[Fill]:
    """Simulated execution at mid +/- half-spread + slippage. Sells first."""
    fills: list[Fill] = []
    for o in sorted(orders, key=lambda o: 0 if o.side == "sell" else 1):
        cf = _cost_frac(instruments[o.ticker], cost)
        mid = prices[o.ticker]
        fee = cost.commission_per_order
        if o.side == "sell":
            px = mid * (1 - cf)
            realized = p.sell(o.ticker, o.qty, px, today, fee)
            if ledger is not None:
                for r in realized:
                    ledger.add_realized(today.year, r)
        else:
            px = mid * (1 + cf)
            q = o.qty
            if q * px + fee > p.cash + 1e-6:  # never go into margin
                q = max((p.cash - fee) / px, 0.0)
            if q <= 1e-12:
                continue
            o = Order(o.ticker, o.side, q, o.est_price, o.reason)
            p.buy(o.ticker, q, px, today, fee)
            realized = []
        fills.append(Fill(o, px, fee, abs(px - mid) * o.qty, realized))
    return fills
