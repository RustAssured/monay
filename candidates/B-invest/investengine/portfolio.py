"""Lot-level portfolio with tax accounting (US rules, simplified)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .config import TaxConfig

WASH_DAYS = 30


@dataclass
class Lot:
    ticker: str
    qty: float
    basis: float          # total cost basis of the lot (incl. buy costs & wash adjustments)
    acquired: date

    @property
    def unit_basis(self) -> float:
        return self.basis / self.qty if self.qty else 0.0


@dataclass
class Realized:
    ticker: str
    qty: float
    proceeds: float
    basis: float
    long_term: bool
    disallowed: float = 0.0   # wash-sale disallowed loss (added to replacement basis)

    @property
    def gain(self) -> float:
        return self.proceeds - self.basis + self.disallowed


@dataclass
class YearTax:
    qualified_div: float = 0.0
    ordinary_income: float = 0.0
    treasury_interest: float = 0.0
    st_net: float = 0.0
    lt_net: float = 0.0


class TaxLedger:
    def __init__(self, cfg: TaxConfig):
        self.cfg = cfg
        self.years: dict[int, YearTax] = {}
        self.st_carry = 0.0  # carried-forward losses (stored as positive numbers)
        self.lt_carry = 0.0
        self.harvested_losses = 0.0
        self.wash_disallowed = 0.0

    def yr(self, y: int) -> YearTax:
        return self.years.setdefault(y, YearTax())

    def add_realized(self, y: int, r: Realized) -> None:
        t = self.yr(y)
        if r.long_term:
            t.lt_net += r.gain
        else:
            t.st_net += r.gain

    def compute_year(self, y: int) -> float:
        """Tax due for year ``y`` (simplified US netting). Updates carryforwards."""
        c = self.cfg
        t = self.yr(y)
        if not c.enabled:
            return 0.0
        st = t.st_net - self.st_carry
        lt = t.lt_net - self.lt_carry
        self.st_carry = self.lt_carry = 0.0
        # cross-netting
        if st < 0 and lt > 0:
            lt, st = lt + st, 0.0
            if lt < 0:
                st, lt = lt, 0.0
        elif lt < 0 and st > 0:
            st, lt = st + lt, 0.0
            if st < 0:
                lt, st = st, 0.0
        ordinary = t.ordinary_income
        offset = 0.0
        if st < 0 or lt < 0:
            loss = -(min(st, 0) + min(lt, 0))
            offset = min(loss, c.ordinary_loss_offset)
            rem = loss - offset
            # carry: ST losses used first against ordinary income
            st_loss = -min(st, 0)
            used_st = min(st_loss, offset)
            self.st_carry = st_loss - used_st
            self.lt_carry = rem - self.st_carry
            st, lt = max(st, 0.0), max(lt, 0.0)
        fed = (
            t.qualified_div * c.qualified_div_rate
            + (ordinary + t.treasury_interest) * c.ordinary_rate
            + st * c.stcg_rate
            + lt * c.ltcg_rate
            - offset * c.ordinary_rate
        )
        state = c.state_rate * (t.qualified_div + ordinary + st + lt - offset)
        return max(fed + state, 0.0)


class Portfolio:
    def __init__(self, cash: float = 0.0):
        self.cash = float(cash)
        self.lots: dict[str, list[Lot]] = {}
        self.last_buy: dict[str, date] = {}
        self.last_loss_sale: dict[str, date] = {}

    # ---- queries -------------------------------------------------------
    def qty(self, ticker: str) -> float:
        return sum(l.qty for l in self.lots.get(ticker, []))

    def tickers(self) -> list[str]:
        return [t for t, ls in self.lots.items() if any(l.qty > 1e-12 for l in ls)]

    def value(self, prices: dict[str, float]) -> float:
        return self.cash + sum(self.qty(t) * prices[t] for t in self.tickers())

    def asset_values(self, prices: dict[str, float], asset_of: dict[str, str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for t in self.tickers():
            a = asset_of[t]
            out[a] = out.get(a, 0.0) + self.qty(t) * prices[t]
        return out

    def unrealized(self, prices: dict[str, float]) -> float:
        return sum(l.qty * prices[t] - l.basis for t in self.tickers() for l in self.lots[t])

    def buy_blocked(self, ticker: str, today: date) -> bool:
        d = self.last_loss_sale.get(ticker)
        return d is not None and (today - d).days <= WASH_DAYS

    # ---- mutations -----------------------------------------------------
    def buy(self, ticker: str, qty: float, price: float, today: date, fee: float = 0.0) -> float:
        cost = qty * price + fee
        self.cash -= cost
        self.lots.setdefault(ticker, []).append(Lot(ticker, qty, cost, today))
        self.last_buy[ticker] = today
        return cost

    def sell(self, ticker: str, qty: float, price: float, today: date, fee: float = 0.0,
             lots: list[Lot] | None = None) -> list[Realized]:
        """Sell ``qty`` shares; lots chosen HIFO (highest unit basis first)
        unless explicit ``lots`` given. Applies the wash-sale rule against
        shares of the same ticker bought within the prior 30 days."""
        held = self.lots.get(ticker, [])
        avail = self.qty(ticker)
        if qty > avail + 1e-9:
            raise ValueError(f"selling {qty} {ticker} but only {avail} held")
        qty = min(qty, avail)
        order = lots if lots is not None else sorted(held, key=lambda l: -l.unit_basis)
        remaining = qty
        out: list[Realized] = []
        gross = qty * price
        for lot in order:
            if remaining <= 1e-12:
                break
            q = min(lot.qty, remaining)
            frac = q / lot.qty
            b = lot.basis * frac
            proceeds = q * price - fee * (q / qty if qty else 0)
            lt = (today - lot.acquired).days > 365
            out.append(Realized(ticker, q, proceeds, b, lt))
            lot.qty -= q
            lot.basis -= b
            remaining -= q
        self.lots[ticker] = [l for l in held if l.qty > 1e-12]
        self.cash += gross - fee
        # wash sale: losses matched against replacement shares bought in the last 30 days
        for r in out:
            if r.gain >= 0:
                continue
            repl = [l for l in self.lots[ticker] if 0 <= (today - l.acquired).days <= WASH_DAYS]
            repl_qty = sum(l.qty for l in repl)
            if repl_qty <= 0:
                continue
            q = min(repl_qty, r.qty)
            dis = -r.gain * (q / r.qty)
            r.disallowed = dis
            for l in repl:
                l.basis += dis * (l.qty / repl_qty)
        if any(r.gain < 0 for r in out):
            self.last_loss_sale[ticker] = today
        return out
