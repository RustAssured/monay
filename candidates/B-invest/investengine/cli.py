"""Command line entry point.

  python -m investengine.cli run --state state.json --prices prices.json \
      --strategy s60_b40_tlh [--deposit 1000] [--execute] [--broker paper|csv]
  python -m investengine.cli backtest      # regenerates results/
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .config import TaxConfig
from .live import CsvOrderBroker, PaperBroker, SafetyError, load_state, portfolio_from_dict, run_once
from .strategies import presets


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="investengine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="plan (and optionally paper-execute) one rebalance")
    r.add_argument("--state", required=True)
    r.add_argument("--prices", required=True, help='JSON {"VTI": 290.1, ...}')
    r.add_argument("--strategy", default="s60_b40", choices=sorted(presets()))
    r.add_argument("--deposit", type=float, default=0.0)
    r.add_argument("--date", default=None)
    r.add_argument("--execute", action="store_true", help="apply orders (paper ledger / csv ticket)")
    r.add_argument("--broker", choices=["paper", "csv"], default="paper")
    r.add_argument("--csv-out", default="orders.csv")
    r.add_argument("--tax-deferred", action="store_true", help="IRA/401k: no taxes, TLH off")
    r.add_argument("--kill-switch", default="STOP")
    sub.add_parser("backtest", help="regenerate results/ (takes a few minutes)")
    a = ap.parse_args(argv)
    if a.cmd == "backtest":
        from .report import main as rmain
        rmain()
        return 0
    prices = json.loads(Path(a.prices).read_text())
    today = date.fromisoformat(a.date) if a.date else date.today()
    tax = TaxConfig(enabled=not a.tax_deferred)
    broker = None
    if a.execute and a.broker == "csv":
        broker = CsvOrderBroker(portfolio_from_dict(load_state(Path(a.state))["portfolio"]), Path(a.csv_out))
    try:
        rep = run_once(Path(a.state), prices, presets()[a.strategy], today, broker=broker,
                       deposit=a.deposit, tax=tax, execute_orders=a.execute,
                       kill_switch=Path(a.kill_switch))
    except SafetyError as e:
        print(f"SAFETY STOP: {e}")
        return 2
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
