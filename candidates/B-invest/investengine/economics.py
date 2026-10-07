"""Forward-looking unit economics: what the engine earns vs what it costs.

All inputs are ASSUMPTIONS (labelled); the historical backtest is used only
to calibrate turnover and tax drag, not to forecast returns."""
from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import product


@dataclass(frozen=True)
class EconAssumptions:
    # forward-looking nominal annual returns (ASSUMED, not forecasts of certainty)
    stock_return: float = 0.065   # US equity; below the 1926-2021 10.3 % history
    bond_return: float = 0.040    # ~ current intermediate Treasury yield (assumed)
    bill_return: float = 0.035    # ~ current T-bill yield (assumed)
    inflation: float = 0.025
    # implementation costs
    expense_ratio: float = 0.0008     # blended, e.g. 60 % VTI 0.03 % + 40 % IEF 0.15 %
    turnover: float = 0.10            # buys+sells / avg value per year, from engine backtest
    trade_cost_bps: float = 2.0       # half-spread + slippage per side
    tax_drag: float = 0.0             # annual return lost to taxes (0 in an IRA)
    bill_tax_rate: float = 0.0        # tax on the T-bill alternative (0 in an IRA)
    fixed_cost_per_year: float = 60.0  # $5/mo VPS (or $0 on an existing machine)
    operator_hours_per_year: float = 4.0
    operator_hourly_value: float = 0.0  # set >0 to price your own time
    # alternative the engine replaces
    alt_fee: float = 0.0025           # typical robo-advisor advisory fee


def portfolio_return(w_stock: float, w_bond: float, a: EconAssumptions) -> float:
    w_bill = 1 - w_stock - w_bond
    gross = w_stock * a.stock_return + w_bond * a.bond_return + w_bill * a.bill_return
    return gross - a.expense_ratio - a.turnover * a.trade_cost_bps / 1e4 - a.tax_drag


def annual_economics(account: float, w_stock: float, w_bond: float, a: EconAssumptions) -> dict:
    r = portfolio_return(w_stock, w_bond, a)
    fixed = a.fixed_cost_per_year + a.operator_hours_per_year * a.operator_hourly_value
    gain = account * r - fixed
    vs_cash = account * (r - a.bill_return * (1 - a.bill_tax_rate)) - fixed
    fee_saved = account * a.alt_fee - fixed
    return {
        "account": account,
        "net_return_rate": r,
        "fixed_costs": fixed,
        "expected_net_gain": gain,
        "expected_real_gain": account * (r - a.inflation) - fixed,
        "expected_gain_vs_tbills": vs_cash,
        "fee_saved_vs_robo": fee_saved,
        "breakeven_account_nominal": fixed / r if r > 0 else float("inf"),
        "breakeven_account_vs_robo": fixed / a.alt_fee if a.alt_fee > 0 else float("inf"),
    }


def sensitivity(account: float = 100_000, w_stock: float = 0.6, w_bond: float = 0.4,
                base: EconAssumptions | None = None) -> list[dict]:
    base = base or EconAssumptions()
    rows = []
    for sr, er, tc, td, fx in product((0.03, 0.05, 0.065, 0.08), (0.0005, 0.0008, 0.003),
                                      (2.0, 10.0), (0.0, 0.008), (0.0, 60.0, 600.0)):
        a = replace(base, stock_return=sr, expense_ratio=er, trade_cost_bps=tc,
                    tax_drag=td, bill_tax_rate=0.24 if td > 0 else 0.0, fixed_cost_per_year=fx)
        e = annual_economics(account, w_stock, w_bond, a)
        rows.append({"stock_return": sr, "expense_ratio": er, "trade_cost_bps": tc,
                     "tax_drag": td, "fixed_cost": fx, **e})
    return rows
