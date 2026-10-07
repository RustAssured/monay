from datetime import date

import pytest

from investengine.config import DEFAULT_INSTRUMENTS, CostConfig, StrategyConfig, TaxConfig
from investengine.portfolio import Portfolio, TaxLedger
from investengine.rebalance import drift_triggered, execute, plan_orders
from investengine.tlh import harvest

PX = {t: 100.0 for t in DEFAULT_INSTRUMENTS}
S6040 = StrategyConfig("t", {"stock": 0.6, "bond": 0.4})
D = date(2024, 1, 31)


def weights(p, px):
    av = p.asset_values(px, {t: i.asset for t, i in DEFAULT_INSTRUMENTS.items()})
    tot = p.value(px)
    return {a: v / tot for a, v in av.items()}


def test_initial_funding_hits_targets_and_no_margin():
    p = Portfolio(100_000)
    o = plan_orders(p, PX, S6040, DEFAULT_INSTRUMENTS, CostConfig(), D, force=True)
    execute(p, o, PX, DEFAULT_INSTRUMENTS, CostConfig(), D)
    w = weights(p, PX)
    assert w["stock"] == pytest.approx(0.6, abs=0.002) and w["bond"] == pytest.approx(0.4, abs=0.002)
    assert p.cash >= -1e-6
    assert {x.ticker for x in o} == {"VTI", "IEF"}


def test_drift_bands():
    assert not drift_triggered({"stock": 0.64, "bond": 0.36}, {"stock": 0.6, "bond": 0.4}, 0.05, 0.25)
    assert drift_triggered({"stock": 0.66, "bond": 0.34}, {"stock": 0.6, "bond": 0.4}, 0.05, 0.25)
    # relative band on a small sleeve: 10 % target drifting to 13 %
    assert drift_triggered({"a": 0.13, "b": 0.87}, {"a": 0.10, "b": 0.90}, 0.05, 0.25)


def test_cash_sweep_never_sells_and_buys_underweight():
    p = Portfolio(100_000)
    execute(p, plan_orders(p, PX, S6040, DEFAULT_INSTRUMENTS, CostConfig(), D, force=True), PX,
            DEFAULT_INSTRUMENTS, CostConfig(), D)
    px = dict(PX, VTI=108.0)  # stocks up -> ~61.8 %, inside bands
    p.cash += 5000
    o = plan_orders(p, px, S6040, DEFAULT_INSTRUMENTS, CostConfig(), D)
    assert all(x.side == "buy" for x in o)
    assert sum(x.notional for x in o if x.ticker == "IEF") > sum(x.notional for x in o if x.ticker == "VTI")


def test_threshold_rebalance_sells_overweight():
    p = Portfolio(100_000)
    execute(p, plan_orders(p, PX, S6040, DEFAULT_INSTRUMENTS, CostConfig(), D, force=True), PX,
            DEFAULT_INSTRUMENTS, CostConfig(), D)
    px = dict(PX, VTI=150.0)
    o = plan_orders(p, px, S6040, DEFAULT_INSTRUMENTS, CostConfig(), D)
    assert any(x.side == "sell" and x.ticker == "VTI" for x in o)
    execute(p, o, px, DEFAULT_INSTRUMENTS, CostConfig(), D)
    assert weights(p, px)["stock"] == pytest.approx(0.6, abs=0.003)


def test_raise_cash_for_tax_bill():
    p = Portfolio(100_000)
    execute(p, plan_orders(p, PX, S6040, DEFAULT_INSTRUMENTS, CostConfig(), D, force=True), PX,
            DEFAULT_INSTRUMENTS, CostConfig(), D)
    p.cash -= 3000
    o = plan_orders(p, PX, S6040, DEFAULT_INSTRUMENTS, CostConfig(), D)
    assert o and all(x.side == "sell" for x in o)
    execute(p, o, PX, DEFAULT_INSTRUMENTS, CostConfig(), D)
    assert p.cash >= 0


def test_whole_shares_mode():
    s = StrategyConfig("w", {"stock": 0.6, "bond": 0.4}, fractional=False)
    p = Portfolio(10_000)
    o = plan_orders(p, {**PX, "VTI": 333.0}, s, DEFAULT_INSTRUMENTS, CostConfig(), D, force=True)
    assert all(float(x.qty).is_integer() for x in o)


def test_risk_off_moves_stocks_to_bills():
    s = StrategyConfig("tr", {"stock": 0.6, "bond": 0.4}, trend_sma=10)
    p = Portfolio(100_000)
    execute(p, plan_orders(p, PX, s, DEFAULT_INSTRUMENTS, CostConfig(), D, risk_off=True, force=True), PX,
            DEFAULT_INSTRUMENTS, CostConfig(), D)
    w = weights(p, PX)
    assert "stock" not in w and w["bill"] == pytest.approx(0.6, abs=0.003)


def test_tlh_harvests_and_switches_to_substitute():
    s = StrategyConfig("t", {"stock": 1.0}, tlh=True)
    p = Portfolio(100_000)
    led = TaxLedger(TaxConfig())
    execute(p, plan_orders(p, PX, s, DEFAULT_INSTRUMENTS, CostConfig(), D, force=True), PX,
            DEFAULT_INSTRUMENTS, CostConfig(), D, led)
    later = date(2024, 4, 30)
    px = dict(PX, VTI=85.0, ITOT=85.0)
    fills = harvest(p, px, s, DEFAULT_INSTRUMENTS, CostConfig(), later, led)
    assert p.qty("VTI") == 0 and p.qty("ITOT") > 0
    assert led.harvested_losses == pytest.approx(15_000, rel=0.01)
    assert led.wash_disallowed == 0
    assert p.buy_blocked("VTI", date(2024, 5, 15))
    # new cash during the wash window must go to the substitute, not VTI
    p.cash += 1000
    o = plan_orders(p, px, s, DEFAULT_INSTRUMENTS, CostConfig(), date(2024, 5, 15))
    assert {x.ticker for x in o} == {"ITOT"}
    assert len(fills) == 2


def test_tlh_ignores_small_losses():
    s = StrategyConfig("t", {"stock": 1.0}, tlh=True)
    p = Portfolio(100_000)
    led = TaxLedger(TaxConfig())
    execute(p, plan_orders(p, PX, s, DEFAULT_INSTRUMENTS, CostConfig(), D, force=True), PX,
            DEFAULT_INSTRUMENTS, CostConfig(), D, led)
    assert harvest(p, dict(PX, VTI=97.0), s, DEFAULT_INSTRUMENTS, CostConfig(), date(2024, 4, 30), led) == []


def test_bad_targets_rejected():
    with pytest.raises(ValueError):
        StrategyConfig("bad", {"stock": 0.7, "bond": 0.4})
