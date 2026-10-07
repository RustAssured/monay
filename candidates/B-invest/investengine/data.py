"""Historical data loading.

Sources (embedded in ./data, no network access at runtime):

* ``shiller_sp500.csv`` - Robert Shiller's monthly S&P composite data as
  repackaged by https://github.com/datasets/s-and-p-500 (data/data.csv).
  Used for: dividend yield (D/P), 10-year Treasury yield ("Long Interest
  Rate", GS10 since 1953), CPI.
* ``ff_factors_monthly.csv`` - Kenneth French Data Library,
  "F-F_Research_Data_Factors" monthly (copy found in the public GitHub repo
  lingyixu/Quant-Finance-With-Python-Code, chapter18/). Used for: US total
  stock-market return (Mkt-RF + RF, CRSP value-weighted, dividends
  reinvested) and the 1-month T-bill return (RF).

Derived (MODELLED, not observed) series:

* 10-year Treasury total return: synthesised from the yield series by
  pricing a par bond each month (constant-maturity approximation).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_shiller(path: Path | None = None) -> pd.DataFrame:
    df = pd.read_csv(path or DATA_DIR / "shiller_sp500.csv", parse_dates=["Date"])
    df = df.drop_duplicates("Date").set_index("Date").sort_index()
    df.index = df.index.to_period("M")
    return df


def load_french(path: Path | None = None) -> pd.DataFrame:
    df = pd.read_csv(path or DATA_DIR / "ff_factors_monthly.csv", dtype={"Date": str})
    df = df[df["Date"].str.fullmatch(r"\d{6}")]
    df.index = pd.PeriodIndex(pd.to_datetime(df["Date"], format="%Y%m"), freq="M")
    df = df.drop(columns="Date").astype(float) / 100.0
    return df


def par_bond_monthly_returns(yield_pct: pd.Series, maturity_years: float = 10.0) -> pd.DataFrame:
    """Total return of a constant-maturity par bond rolled monthly.

    At t-1 buy a par bond with coupon = y_{t-1}, maturity N years (monthly
    compounding). At t, value it with N - 1/12 years remaining at yield y_t.
    Returns DataFrame with columns ``tr`` (total) and ``inc`` (coupon income).
    """
    y = yield_pct / 100.0
    c = y.shift(1)
    n = maturity_years * 12 - 1
    m = y / 12.0
    with np.errstate(divide="ignore", invalid="ignore"):
        annuity = np.where(m > 0, (1 - (1 + m) ** (-n)) / m, n)
    price = (c / 12.0) * annuity + (1 + m) ** (-n)
    inc = c / 12.0
    tr = price - 1.0 + inc
    return pd.DataFrame({"tr": tr, "inc": inc}, index=yield_pct.index)


def build_dataset(start: str = "1926-07", end: str = "2021-06") -> pd.DataFrame:
    """Monthly joint dataset of asset returns.

    Columns:
      stock_tr, stock_inc  US total market total return / dividend income part
      bond_tr,  bond_inc   10y Treasury (modelled) total return / coupon part
      bill_tr              1-month T-bill return (all income)
      infl                 CPI inflation
    """
    sh = load_shiller()
    ff = load_french()
    bond = par_bond_monthly_returns(sh["Long Interest Rate"].replace(0, np.nan))
    out = pd.DataFrame(index=ff.index)
    out["stock_tr"] = ff["Mkt-RF"] + ff["RF"]
    dy = (sh["Dividend"] / sh["SP500"]).replace(0, np.nan)
    out["stock_inc"] = (dy / 12.0).reindex(out.index)
    out["bond_tr"] = bond["tr"].reindex(out.index)
    out["bond_inc"] = bond["inc"].reindex(out.index)
    out["bill_tr"] = ff["RF"]
    cpi = sh["Consumer Price Index"].replace(0, np.nan)
    out["infl"] = cpi.pct_change().reindex(out.index)
    out = out.loc[start:end]
    if out.isna().any().any():
        bad = out[out.isna().any(axis=1)]
        raise ValueError(f"missing data in dataset: {bad.index[:5].tolist()}")
    return out


def shiller_long_equity(start: str = "1871-02", end: str = "2023-06") -> pd.DataFrame:
    """Long-run (1871+) S&P composite total return from Shiller (price from
    monthly *average* prices, so volatility/drawdowns are understated)."""
    sh = load_shiller().loc[start:end]
    sh = sh[(sh["Dividend"] > 0) & (sh["Consumer Price Index"] > 0)]
    p = sh["SP500"]
    tr = p / p.shift(1) - 1 + (sh["Dividend"] / p) / 12.0
    infl = sh["Consumer Price Index"].pct_change()
    return pd.DataFrame({"stock_tr": tr, "infl": infl}).dropna()
