from datetime import date

import pytest

from investengine.config import TaxConfig
from investengine.portfolio import Portfolio, Realized, TaxLedger


def test_hifo_and_holding_period():
    p = Portfolio(10_000)
    p.buy("X", 10, 100, date(2020, 1, 1))
    p.buy("X", 10, 150, date(2021, 6, 1))
    r = p.sell("X", 10, 120, date(2021, 12, 1))
    assert len(r) == 1 and r[0].basis == pytest.approx(1500)  # highest basis first
    assert not r[0].long_term and r[0].gain == pytest.approx(-300)
    r2 = p.sell("X", 10, 120, date(2021, 12, 2))
    assert r2[0].long_term and r2[0].gain == pytest.approx(200)
    assert p.qty("X") == 0


def test_cannot_oversell():
    p = Portfolio(1000)
    p.buy("X", 1, 100, date(2020, 1, 1))
    with pytest.raises(ValueError):
        p.sell("X", 2, 100, date(2020, 2, 1))


def test_wash_sale_disallows_and_adjusts_basis():
    p = Portfolio(10_000)
    p.buy("X", 10, 100, date(2020, 1, 1))
    p.buy("X", 5, 80, date(2020, 3, 10))              # replacement shares within 30 days
    old = [l for l in p.lots["X"] if l.acquired == date(2020, 1, 1)]
    r = p.sell("X", 10, 80, date(2020, 3, 20), lots=old)
    assert r[0].proceeds - r[0].basis == pytest.approx(-200)
    assert r[0].disallowed == pytest.approx(100)        # 5 of 10 shares replaced
    assert r[0].gain == pytest.approx(-100)
    assert p.lots["X"][0].basis == pytest.approx(400 + 100)
    assert p.buy_blocked("X", date(2020, 4, 1)) and not p.buy_blocked("X", date(2020, 4, 25))


def L(y, gain, lt):
    return Realized("X", 1, gain if gain > 0 else 0, 0 if gain > 0 else -gain, lt)


def test_tax_netting_and_loss_limit_carryforward():
    cfg = TaxConfig(ordinary_rate=0.24, ltcg_rate=0.15, stcg_rate=0.24, qualified_div_rate=0.15)
    led = TaxLedger(cfg)
    led.add_realized(2020, L(2020, -10_000, True))
    led.yr(2020).ordinary_income = 1000
    t = led.compute_year(2020)
    # 1000*24% - 3000*24% offset -> clipped at zero; 7000 LT carry
    assert t == 0.0 and led.lt_carry == pytest.approx(7000)
    led.add_realized(2021, L(2021, 10_000, True))
    assert led.compute_year(2021) == pytest.approx(3000 * 0.15)


def test_tax_rates_and_state_exemption():
    cfg = TaxConfig(state_rate=0.05)
    led = TaxLedger(cfg)
    y = led.yr(2022)
    y.qualified_div, y.treasury_interest = 1000, 1000
    led.add_realized(2022, L(2022, 1000, False))
    expect = 1000 * 0.15 + 1000 * 0.24 + 1000 * 0.24 + 0.05 * (1000 + 1000)  # treasury interest state-exempt
    assert led.compute_year(2022) == pytest.approx(expect)


def test_tax_disabled():
    led = TaxLedger(TaxConfig(enabled=False))
    led.yr(2022).ordinary_income = 1e6
    assert led.compute_year(2022) == 0
