import csv
import json
from datetime import date

import pytest

from investengine.cli import main as cli_main
from investengine.config import StrategyConfig, TaxConfig
from investengine.live import (AlpacaBroker, CsvOrderBroker, Portfolio, SafetyError, load_state,
                               portfolio_from_dict, portfolio_to_dict, run_once, save_state)
from investengine.rebalance import Order

PX = {"VTI": 250.0, "ITOT": 110.0, "IEF": 95.0, "VGIT": 58.0, "SGOV": 100.5, "BIL": 91.6}
S = StrategyConfig("s", {"stock": 0.6, "bond": 0.4}, tlh=True)


def test_state_roundtrip(tmp_path):
    p = Portfolio(123.0)
    p.buy("VTI", 1.5, 200, date(2024, 1, 2))
    p.last_loss_sale["IEF"] = date(2024, 1, 3)
    q = portfolio_from_dict(json.loads(json.dumps(portfolio_to_dict(p))))
    assert q.qty("VTI") == 1.5 and q.cash == p.cash and q.last_loss_sale == p.last_loss_sale


def test_dry_run_does_not_touch_state(tmp_path):
    sp = tmp_path / "s.json"
    rep = run_once(sp, PX, S, date(2024, 1, 2), deposit=10_000)
    assert not rep["executed"] and rep["orders"] and not sp.exists()


def test_execute_paper_then_idempotent(tmp_path):
    sp = tmp_path / "s.json"
    rep = run_once(sp, PX, S, date(2024, 1, 2), deposit=10_000, execute_orders=True)
    st = load_state(sp)
    p = portfolio_from_dict(st["portfolio"])
    assert rep["executed"] and p.qty("VTI") * PX["VTI"] == pytest.approx(6000, rel=0.01)
    with pytest.raises(SafetyError):
        run_once(sp, PX, S, date(2024, 1, 2), execute_orders=True)
    rep2 = run_once(sp, PX, S, date(2024, 2, 1), execute_orders=True)
    assert rep2["orders"] == []  # nothing to do


def test_safety_guards(tmp_path):
    sp = tmp_path / "s.json"
    run_once(sp, PX, S, date(2024, 1, 2), deposit=10_000, execute_orders=True)
    with pytest.raises(SafetyError):
        run_once(sp, {**PX, "IEF": 0.0}, S, date(2024, 2, 1))
    with pytest.raises(SafetyError):
        run_once(sp, {**PX, "VTI": 400.0}, S, date(2024, 2, 1))  # +60 % stale/bad quote
    stop = tmp_path / "STOP"
    stop.write_text("x")
    with pytest.raises(SafetyError):
        run_once(sp, PX, S, date(2024, 2, 1), kill_switch=stop)
    # switching to an all-bills preset would sell 100 % -> blocked by default
    with pytest.raises(SafetyError):
        run_once(sp, PX, StrategyConfig("b", {"bill": 1.0}), date(2024, 2, 1))


def test_reconciliation_against_broker(tmp_path):
    sp = tmp_path / "s.json"
    run_once(sp, PX, S, date(2024, 1, 2), deposit=10_000, execute_orders=True)
    fake = lambda m, path, body: {"positions": [{"symbol": "VTI", "qty": "1"}]}
    with pytest.raises(SafetyError, match="mismatch"):
        run_once(sp, PX, S, date(2024, 2, 1), broker=AlpacaBroker(transport=fake))


def test_alpaca_payload():
    calls = []
    b = AlpacaBroker(transport=lambda m, path, body: calls.append((m, path, body)) or {"id": "1"})
    b.submit(Order("VTI", "buy", 1.234567891, 250, "rebalance"))
    m, path, body = calls[0]
    assert (m, path) == ("POST", "/v2/orders")
    assert body["symbol"] == "VTI" and body["qty"] == "1.234568" and body["type"] == "market"
    assert "paper-api" in b.base_url


def test_csv_ticket(tmp_path):
    out = tmp_path / "orders.csv"
    b = CsvOrderBroker(Portfolio(), out)
    run_once(tmp_path / "s.json", PX, S, date(2024, 1, 2), broker=b, deposit=5000, execute_orders=True)
    rows = list(csv.DictReader(out.open()))
    assert {r["ticker"] for r in rows} == {"VTI", "IEF"}


def test_cli_end_to_end(tmp_path, capsys):
    prices = tmp_path / "p.json"
    prices.write_text(json.dumps(PX))
    sp = tmp_path / "s.json"
    rc = cli_main(["run", "--state", str(sp), "--prices", str(prices), "--deposit", "1000",
                   "--date", "2024-01-02", "--execute", "--kill-switch", str(tmp_path / "STOP")])
    assert rc == 0 and sp.exists()
    rc = cli_main(["run", "--state", str(sp), "--prices", str(prices), "--date", "2024-01-02",
                   "--execute", "--kill-switch", str(tmp_path / "STOP")])
    assert rc == 2 and "SAFETY STOP" in capsys.readouterr().out
