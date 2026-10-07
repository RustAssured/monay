"""Live / paper operation: broker adapters, state persistence, safety guards.

The engine is broker-agnostic. It needs only: positions, cash, quotes, and a
way to submit market/marketable orders. Three adapters are provided:

* ``PaperBroker``    - simulated fills, state in a JSON file (default).
* ``CsvOrderBroker`` - writes an order ticket CSV for a human to enter at any
                       broker (works everywhere, zero API risk).
* ``AlpacaBroker``   - REST adapter for Alpaca's trading API. The HTTP layer is
                       injectable; it is unit-tested only against a fake
                       transport, NOT against the live API (see REPORT.md).
"""
from __future__ import annotations

import csv
import json
import os
from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Callable

from .config import DEFAULT_INSTRUMENTS, CostConfig, Instrument, StrategyConfig, TaxConfig
from .portfolio import Lot, Portfolio, TaxLedger
from .rebalance import Order, execute, plan_orders
from .tlh import harvest


class SafetyError(RuntimeError):
    pass


# ---------------------------------------------------------------- state I/O
def portfolio_to_dict(p: Portfolio) -> dict:
    return {
        "cash": p.cash,
        "lots": [{**asdict(l), "acquired": l.acquired.isoformat()} for ls in p.lots.values() for l in ls],
        "last_buy": {k: v.isoformat() for k, v in p.last_buy.items()},
        "last_loss_sale": {k: v.isoformat() for k, v in p.last_loss_sale.items()},
    }


def portfolio_from_dict(d: dict) -> Portfolio:
    p = Portfolio(d.get("cash", 0.0))
    for l in d.get("lots", []):
        p.lots.setdefault(l["ticker"], []).append(
            Lot(l["ticker"], float(l["qty"]), float(l["basis"]), date.fromisoformat(l["acquired"])))
    p.last_buy = {k: date.fromisoformat(v) for k, v in d.get("last_buy", {}).items()}
    p.last_loss_sale = {k: date.fromisoformat(v) for k, v in d.get("last_loss_sale", {}).items()}
    return p


def load_state(path: Path) -> dict:
    if not Path(path).exists():
        return {"portfolio": portfolio_to_dict(Portfolio()), "last_run": None, "history": []}
    return json.loads(Path(path).read_text())


def save_state(path: Path, state: dict) -> None:
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, path)  # atomic


# ---------------------------------------------------------------- brokers
class Broker(ABC):
    @abstractmethod
    def positions(self) -> dict[str, float]: ...

    @abstractmethod
    def cash(self) -> float: ...

    @abstractmethod
    def submit(self, order: Order) -> dict: ...


class PaperBroker(Broker):
    """Holds no state of its own: positions mirror the engine ledger."""

    def __init__(self, portfolio: Portfolio):
        self.p = portfolio
        self.submitted: list[Order] = []

    def positions(self) -> dict[str, float]:
        return {t: self.p.qty(t) for t in self.p.tickers()}

    def cash(self) -> float:
        return self.p.cash

    def submit(self, order: Order) -> dict:
        self.submitted.append(order)
        return {"status": "simulated", "ticker": order.ticker, "side": order.side, "qty": order.qty}


class CsvOrderBroker(PaperBroker):
    def __init__(self, portfolio: Portfolio, out_path: Path):
        super().__init__(portfolio)
        self.out_path = Path(out_path)

    def flush(self) -> Path:
        with self.out_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ticker", "side", "qty", "est_price", "est_notional", "reason"])
            for o in self.submitted:
                w.writerow([o.ticker, o.side, f"{o.qty:.6f}", f"{o.est_price:.4f}",
                            f"{o.notional:.2f}", o.reason])
        return self.out_path


Transport = Callable[[str, str, dict | None], dict]


class AlpacaBroker(Broker):
    """Minimal Alpaca v2 REST adapter (fractional market orders, day TIF).
    ``transport(method, path, json_body) -> dict`` performs the HTTP call; a
    urllib-based default is used when none is given. Keys come from env vars
    APCA_API_KEY_ID / APCA_API_SECRET_KEY; base URL defaults to PAPER trading."""

    def __init__(self, transport: Transport | None = None,
                 base_url: str = "https://paper-api.alpaca.markets"):
        self.base_url = base_url
        self.transport = transport or self._urllib_transport

    def _urllib_transport(self, method: str, path: str, body: dict | None) -> dict:  # pragma: no cover
        import urllib.request
        req = urllib.request.Request(self.base_url + path, method=method,
                                     data=json.dumps(body).encode() if body else None)
        req.add_header("APCA-API-KEY-ID", os.environ["APCA_API_KEY_ID"])
        req.add_header("APCA-API-SECRET-KEY", os.environ["APCA_API_SECRET_KEY"])
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")

    def positions(self) -> dict[str, float]:
        res = self.transport("GET", "/v2/positions", None)
        return {p["symbol"]: float(p["qty"]) for p in res.get("positions", res if isinstance(res, list) else [])}

    def cash(self) -> float:
        return float(self.transport("GET", "/v2/account", None)["cash"])

    def submit(self, order: Order) -> dict:
        body = {"symbol": order.ticker, "qty": f"{order.qty:.6f}", "side": order.side,
                "type": "market", "time_in_force": "day",
                "client_order_id": f"ie-{order.ticker}-{order.side}-{order.reason}"[:48]}
        return self.transport("POST", "/v2/orders", body)


# ---------------------------------------------------------------- run once
def check_prices(prices: dict[str, float], needed: set[str], prev: dict[str, float] | None,
                 max_move: float = 0.25) -> None:
    for t in needed:
        px = prices.get(t)
        if px is None or not (px > 0):
            raise SafetyError(f"missing/invalid price for {t}: {px}")
        if prev and t in prev and abs(px / prev[t] - 1) > max_move:
            raise SafetyError(f"{t} price moved {px / prev[t] - 1:+.0%} since last run; "
                              "verify the quote and re-run with a fresh prices file")


def run_once(state_path: Path, prices: dict[str, float], strategy: StrategyConfig,
             today: date, broker: Broker | None = None, deposit: float = 0.0,
             tax: TaxConfig | None = None, cost: CostConfig | None = None,
             instruments: dict[str, Instrument] | None = None, execute_orders: bool = False,
             max_sell_fraction: float = 0.5, kill_switch: Path | None = None) -> dict:
    """One scheduled engine run: reconcile -> TLH -> rebalance -> (paper) execute.

    ``execute_orders=False`` (default) is a dry run: orders are planned and
    returned but nothing is submitted and state is not modified."""
    if kill_switch is not None and Path(kill_switch).exists():
        raise SafetyError(f"kill switch present: {kill_switch}")
    tax, cost = tax or TaxConfig(), cost or CostConfig()
    instruments = instruments or DEFAULT_INSTRUMENTS
    state = load_state(state_path)
    if execute_orders and state.get("last_run") == today.isoformat():
        raise SafetyError("engine already executed today; refusing to double-trade")
    p = portfolio_from_dict(state["portfolio"])
    p.cash += deposit
    needed = {t for a in strategy.targets for t in strategy.tickers[a]} | set(p.tickers())
    check_prices(prices, needed, state.get("last_prices"))
    insts = {t: instruments[t] for t in needed}

    if broker is not None and not isinstance(broker, PaperBroker):
        held = broker.positions()
        for t in set(held) | set(p.tickers()):
            if abs(held.get(t, 0.0) - p.qty(t)) > 1e-4:
                raise SafetyError(f"position mismatch for {t}: broker {held.get(t, 0)} vs ledger {p.qty(t)}")

    ledger = TaxLedger(tax)
    sim = portfolio_from_dict(portfolio_to_dict(p))
    fills = []
    if strategy.tlh and tax.enabled:
        fills += harvest(sim, prices, strategy, insts, cost, today, ledger)
    orders = plan_orders(sim, prices, strategy, insts, cost, today)
    sells = sum(f.order.notional for f in fills if f.order.side == "sell") + \
        sum(o.notional for o in orders if o.side == "sell")
    total = p.value(prices)
    if total > 0 and sells / total > max_sell_fraction:
        raise SafetyError(f"planned sells {sells / total:.0%} of portfolio exceed limit {max_sell_fraction:.0%}")
    fills += execute(sim, orders, prices, insts, cost, today, ledger)
    all_orders = [f.order for f in fills]
    report = {
        "date": today.isoformat(),
        "value_before": total,
        "value_after_est": sim.value(prices),
        "orders": [{"ticker": o.ticker, "side": o.side, "qty": round(o.qty, 6),
                    "est_notional": round(o.notional, 2), "reason": o.reason} for o in all_orders],
        "realized": [{"ticker": r.ticker, "gain": round(r.gain, 2), "long_term": r.long_term,
                      "wash_disallowed": round(r.disallowed, 2)} for f in fills for r in f.realized],
        "executed": False,
    }
    if execute_orders:
        broker = broker or PaperBroker(sim)
        report["broker_responses"] = [broker.submit(o) for o in all_orders]
        if isinstance(broker, CsvOrderBroker):
            broker.flush()
        # Paper: ledger becomes the simulated post-trade portfolio. For a real
        # broker the operator must confirm fills; the next run reconciles.
        state["portfolio"] = portfolio_to_dict(sim)
        state["last_run"] = today.isoformat()
        state["last_prices"] = prices
        state.setdefault("history", []).append({k: report[k] for k in ("date", "value_before", "orders", "realized")})
        save_state(state_path, state)
        report["executed"] = True
    return report
