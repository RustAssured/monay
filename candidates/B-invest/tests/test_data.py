import numpy as np
import pandas as pd
import pytest

from investengine.data import build_dataset, par_bond_monthly_returns, shiller_long_equity


@pytest.fixture(scope="module")
def data():
    return build_dataset()


def test_dataset_complete(data):
    assert len(data) == 1140
    assert not data.isna().any().any()


def _cagr(x):
    return np.prod(1 + x) ** (12 / len(x)) - 1


def test_dataset_matches_published_history(data):
    # Published long-run US figures 1926-2020s: stocks ~10%, T-bills ~3.3%, CPI ~2.9%
    assert 0.095 < _cagr(data.stock_tr) < 0.11
    assert 0.03 < _cagr(data.bill_tr) < 0.036
    assert 0.026 < _cagr(data.infl) < 0.032
    assert 0.045 < _cagr(data.bond_tr) < 0.06


def test_par_bond_constant_yield_returns_coupon():
    y = pd.Series([5.0] * 24)
    r = par_bond_monthly_returns(y)
    assert np.allclose(r["tr"].iloc[1:], 0.05 / 12, atol=1e-12)


def test_par_bond_loses_when_yields_rise():
    r = par_bond_monthly_returns(pd.Series([4.0, 5.0]))
    assert r["tr"].iloc[1] < -0.05  # ~ -7.7 % for a 10y duration bond


def test_shiller_long_series():
    l = shiller_long_equity()
    assert l.index[0].year == 1871 and len(l) > 1800
