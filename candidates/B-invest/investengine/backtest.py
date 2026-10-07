"""Monthly event-driven backtester that drives the *same* engine code used
for live/paper order generation (plan_orders / execute / harvest)."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .config import DEFAULT_INSTRUMENTS, CostConfig, Instrument, StrategyConfig, TaxConfig
from .portfolio import Portfolio, TaxLedger
from .rebalance import execute, plan_orders
from .tlh import harvest

ASSET_COLS = {"stock": ("stock_tr", "stock_inc"), "bond": ("bond_tr", "bond_inc"),
              "bill": ("bill_tr", "bill_tr")}


@dataclass
class BacktestResult:
    strategy: str
    values: pd.Series                 # month-end portfolio value (pre-liquidation)
    contributions: pd.Series          # cumulative external cash added
    liquidation_value: float          # after selling everything & paying all tax
    taxes_paid: float
    trading_costs: float              # spread + slippage + commissions
    expense_drag: float               # fund expense ratios paid
    turnover: float                   # total traded notional / avg value / years
    harvested_losses: float
    wash_disallowed: float
    n_trades: int
    risk_off_months: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def monthly_returns(self) -> pd.Series:
        """Time-weighted returns (contributions removed)."""
        v = self.values
        flows = self.contributions.diff().fillna(self.contributions.iloc[0])
        prev = v.shift(1)
        r = (v - flows) / prev - 1
        return r.iloc[1:]


def month_end(p: pd.Period) -> date:
    return p.to_timestamp(how="end").date()


def trend_signal(stock_tr: pd.Series, n: int) -> pd.Series:
    """True = risk-off. Uses data up to and including month t only."""
    idx = (1 + stock_tr).cumprod()
    sma = idx.rolling(n, min_periods=n).mean()
    return (idx < sma).fillna(False)


def run_backtest(data: pd.DataFrame, strategy: StrategyConfig, initial: float = 100_000.0,
                 monthly_contribution: float = 0.0, tax: TaxConfig | None = None,
                 cost: CostConfig | None = None,
                 instruments: dict[str, Instrument] | None = None,
                 signal_data: pd.DataFrame | None = None) -> BacktestResult:
    """``signal_data``: longer history (ending at or after ``data``) used only to
    warm up the trend signal; the signal at month t uses data <= t."""
    tax = tax or TaxConfig()
    cost = cost or CostConfig()
    instruments = instruments or DEFAULT_INSTRUMENTS
    assets = set(strategy.targets) | ({"bill"} if strategy.trend_sma else set())
    used = {t for a in assets for t in strategy.tickers[a]}
    insts = {t: instruments[t] for t in used}
    asset_of = {t: i.asset for t, i in insts.items()}

    p = Portfolio(cash=initial)
    ledger = TaxLedger(tax)
    prices = {t: 100.0 for t in insts}
    src = signal_data if signal_data is not None else data
    risk = trend_signal(src["stock_tr"], strategy.trend_sma) if strategy.trend_sma else None

    values, contribs = [], []
    total_contrib = initial
    taxes_paid = trading = expense = traded = 0.0
    n_trades = 0
    risk_off_months = 0
    prev_risk = False
    start = month_end(data.index[0])
    # invest initial cash at the first month-end using that month's prices as entry
    first = True
    months_since_rebal = 0
    for per, row in data.iterrows():
        today = month_end(per)
        if not first:
            # 1) market move + distributions
            for t, inst in insts.items():
                tr_col, inc_col = ASSET_COLS[inst.asset]
                tr, inc = row[tr_col], row[inc_col]
                q = p.qty(t)
                old = prices[t]
                expense += q * old * inst.expense_ratio / 12
                prices[t] = old * (1 + tr - inc - inst.expense_ratio / 12)
                dist = q * old * inc
                if q > 0:
                    p.cash += dist
                    y = ledger.yr(today.year)
                    if inst.asset == "stock":
                        y.qualified_div += dist * tax.qualified_div_fraction
                        y.ordinary_income += dist * (1 - tax.qualified_div_fraction)
                    else:
                        y.treasury_interest += dist
            # 2) tax-loss harvesting
            if strategy.tlh and tax.enabled:
                fills = harvest(p, prices, strategy, insts, cost, today, ledger)
                for f in fills:
                    trading += f.cost + f.fee
                    traded += f.order.qty * f.price
                n_trades += len(fills)
            # 3) year-end tax bill paid from the portfolio
            if today.month == 12:
                due = ledger.compute_year(today.year)
                p.cash -= due
                taxes_paid += due
            # 4) contributions
            if monthly_contribution:
                p.cash += monthly_contribution
                total_contrib += monthly_contribution
        # 5) rebalance / sweep
        ro = bool(risk.loc[per]) if risk is not None else False
        risk_off_months += ro
        months_since_rebal += 1
        force = first or (ro != prev_risk) or (
            strategy.calendar_months is not None and months_since_rebal >= strategy.calendar_months)
        orders = plan_orders(p, prices, strategy, insts, cost, today, risk_off=ro, force=force)
        if force:
            months_since_rebal = 0
        fills = execute(p, orders, prices, insts, cost, today, ledger)
        for f in fills:
            trading += f.cost + f.fee
            traded += f.order.qty * f.price
        n_trades += len(fills)
        prev_risk = ro
        first = False
        values.append(p.value(prices))
        contribs.append(total_contrib)

    idx = data.index
    vals = pd.Series(values, index=idx)
    liq = liquidation_value(p, prices, ledger, insts, cost, month_end(idx[-1]))
    years = len(idx) / 12
    return BacktestResult(
        strategy=strategy.name, values=vals, contributions=pd.Series(contribs, index=idx),
        liquidation_value=liq, taxes_paid=taxes_paid, trading_costs=trading,
        expense_drag=expense, turnover=traded / max(vals.mean(), 1e-9) / years,
        harvested_losses=ledger.harvested_losses, wash_disallowed=ledger.wash_disallowed,
        n_trades=n_trades, risk_off_months=risk_off_months,
    )


def liquidation_value(p: Portfolio, prices, ledger: TaxLedger, insts, cost: CostConfig,
                      today: date) -> float:
    """Cash left after selling everything at the end and paying the
    current year's remaining tax (including on the liquidation itself)."""
    p2, l2 = copy.deepcopy(p), copy.deepcopy(ledger)
    # December: this year's tax was already paid -> treat the sale as next year's.
    tax_year = today.year + 1 if today.month == 12 else today.year
    for t in p2.tickers():
        q = p2.qty(t)
        cf = (insts[t].half_spread_bps * cost.spread_multiplier + cost.slippage_bps) / 1e4
        for r in p2.sell(t, q, prices[t] * (1 - cf), today, cost.commission_per_order):
            l2.add_realized(tax_year, r)
    return p2.cash - l2.compute_year(tax_year)
