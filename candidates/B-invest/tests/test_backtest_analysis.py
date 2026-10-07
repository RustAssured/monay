import numpy as np
import pandas as pd
import pytest

from investengine.analysis import (bootstrap_loss_probs, cagr, max_drawdown, rolling_loss_probs,
                                   stationary_bootstrap_idx, walk_forward)
from investengine.backtest import run_backtest, trend_signal
from investengine.config import DEFAULT_INSTRUMENTS, CostConfig, Instrument, StrategyConfig, TaxConfig
from investengine.data import build_dataset
from investengine.economics import EconAssumptions, annual_economics, sensitivity
from investengine.report import irr_monthly


@pytest.fixture(scope="module")
def data():
    return build_dataset()


def test_frictionless_backtest_equals_index(data):
    """With zero costs, zero fees and no tax, the engine must reproduce the index exactly."""
    insts = {t: Instrument(t, i.asset, 0.0, 0.0) for t, i in DEFAULT_INSTRUMENTS.items()}
    res = run_backtest(data, StrategyConfig("s", {"stock": 1.0}), tax=TaxConfig(enabled=False),
                       cost=CostConfig(0, 0, 0), instruments=insts)
    assert cagr(res.monthly_returns) == pytest.approx(cagr(data.stock_tr.iloc[1:]), abs=1e-6)


def test_costs_and_taxes_reduce_returns(data):
    d = data.loc["1990":"2010"]
    s = StrategyConfig("s", {"stock": 0.6, "bond": 0.4})
    free = run_backtest(d, s, tax=TaxConfig(enabled=False))
    taxed = run_backtest(d, s, tax=TaxConfig())
    assert taxed.liquidation_value < free.liquidation_value
    assert taxed.taxes_paid > 0 and free.trading_costs > 0 and free.expense_drag > 0


def test_contributions_tracked(data):
    d = data.loc["2000":"2004"]
    res = run_backtest(d, StrategyConfig("s", {"stock": 1.0}), monthly_contribution=500,
                       tax=TaxConfig(enabled=False))
    assert res.contributions.iloc[-1] == pytest.approx(100_000 + 500 * (len(d) - 1))
    assert -0.2 < irr_monthly(res) < 0.2


def test_trend_signal_has_no_lookahead(data):
    s = data.stock_tr.copy()
    a = trend_signal(s, 10)
    s2 = s.copy()
    s2.iloc[600:] = -0.5  # destroy the future
    b = trend_signal(s2, 10)
    assert (a.iloc[:600] == b.iloc[:600]).all()


def test_max_drawdown_and_rolling():
    r = pd.Series([0.1, -0.5, 0.2, 0.2, 1.0])
    dd, uw = max_drawdown(r)
    assert dd == pytest.approx(-0.5) and uw == 3
    z = pd.Series([0.01] * 130)
    rp = rolling_loss_probs(z, z * 0, z * 0)
    assert rp["1y"]["p_nominal_loss"] == 0 and rp["10y"]["p_below_bills"] == 0


def test_bootstrap_deterministic_and_sane(data):
    idx = stationary_bootstrap_idx(100, 50, 30, 10, np.random.default_rng(0))
    assert idx.shape == (50, 30) and idx.min() >= 0 and idx.max() < 100
    a = bootstrap_loss_probs(data, {"stock": 0.6, "bond": 0.4}, 0.001, n_paths=2000)
    b = bootstrap_loss_probs(data, {"stock": 0.6, "bond": 0.4}, 0.001, n_paths=2000)
    assert a == b
    assert a["10y"]["p_nominal_loss"] < a["1y"]["p_nominal_loss"]
    worse = bootstrap_loss_probs(data, {"stock": 0.6, "bond": 0.4}, 0.001, n_paths=2000, stock_haircut=0.05)
    assert worse["10y"]["p_nominal_loss"] >= a["10y"]["p_nominal_loss"]


def test_walk_forward_smoke(data):
    d = data.loc[:"1975-12"]
    base = StrategyConfig("base", {"stock": 0.6, "bond": 0.4})
    alt = StrategyConfig("trend10", {"stock": 0.6, "bond": 0.4}, trend_sma=10)
    wf = walk_forward(d, base, [base, alt], first_test_year=1951, step_years=10)
    assert len(wf["blocks"]) == 3
    assert len(wf["selected"]) == len(wf["base"]) > 250


def test_economics():
    a = EconAssumptions()
    e = annual_economics(100_000, 0.6, 0.4, a)
    assert e["breakeven_account_nominal"] == pytest.approx(a.fixed_cost_per_year / e["net_return_rate"])
    assert e["breakeven_account_vs_robo"] == pytest.approx(60 / 0.0025)
    lo = annual_economics(100_000, 0.6, 0.4, EconAssumptions(stock_return=0.03))
    assert lo["expected_net_gain"] < e["expected_net_gain"]
    assert len(sensitivity()) == 4 * 3 * 2 * 2 * 3
