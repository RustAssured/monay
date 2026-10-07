"""Tax-loss harvesting with wash-sale avoidance via a substitute ETF.

ASSUMPTION / RISK: the substitute (e.g. VTI <-> ITOT, both total-US-market
funds tracking *different* indexes) is treated by the operator as not
"substantially identical". The IRS has never ruled precisely on this;
the operator should take tax advice. Wash sales triggered in OTHER accounts
(IRA, spouse) are not visible to this engine.
"""
from __future__ import annotations

from datetime import date

from .config import CostConfig, Instrument, StrategyConfig
from .portfolio import WASH_DAYS, Portfolio, TaxLedger
from .rebalance import Fill, Order, _cost_frac, execute


def harvest(p: Portfolio, prices: dict[str, float], strategy: StrategyConfig,
            instruments: dict[str, Instrument], cost: CostConfig, today: date,
            ledger: TaxLedger) -> list[Fill]:
    fills: list[Fill] = []
    for asset, tickers in strategy.tickers.items():
        for t in list(tickers):
            lots = p.lots.get(t, [])
            if not lots:
                continue
            px = prices[t]
            loss_lots = [l for l in lots if px < l.unit_basis * (1 - strategy.tlh_threshold)]
            if not loss_lots:
                continue
            # also sell recent lots that are at any loss, so they are not wash "replacements"
            chosen = {id(l) for l in loss_lots}
            loss_lots += [l for l in lots if id(l) not in chosen and px < l.unit_basis
                          and (today - l.acquired).days <= WASH_DAYS]
            loss = sum(l.basis - l.qty * px for l in loss_lots)
            if loss < strategy.tlh_min_loss:
                continue
            # expected loss must exceed round-trip trading cost by a wide margin
            qty = sum(l.qty for l in loss_lots)
            subs = [s for s in tickers if s != t and not p.buy_blocked(s, today)]
            if not subs:
                continue
            sub = subs[0]
            rt_cost = qty * px * (_cost_frac(instruments[t], cost) + _cost_frac(instruments[sub], cost))
            if loss * 0.15 < 3 * rt_cost:  # at a nominal 15 % tax value
                continue
            cf = _cost_frac(instruments[t], cost)
            realized = p.sell(t, qty, px * (1 - cf), today, cost.commission_per_order, lots=loss_lots)
            for r in realized:
                ledger.add_realized(today.year, r)
                if r.gain < 0:
                    ledger.harvested_losses += -r.gain
                ledger.wash_disallowed += r.disallowed
            sold = Order(t, "sell", qty, px, "tlh")
            fills.append(Fill(sold, px * (1 - cf), cost.commission_per_order, cf * px * qty, realized))
            proceeds = qty * px * (1 - cf) - cost.commission_per_order
            sp = prices[sub]
            sq = proceeds / (sp * (1 + _cost_frac(instruments[sub], cost))) - 1e-9
            fills += execute(p, [Order(sub, "buy", sq, sp, "tlh-replace")], prices,
                             instruments, cost, today, ledger)
    return fills
