"""Performance statistics, loss probabilities, bootstrap and walk-forward."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from .backtest import BacktestResult, run_backtest
from .config import CostConfig, StrategyConfig, TaxConfig


def cagr(r: pd.Series) -> float:
    return float(np.prod(1 + r) ** (12 / len(r)) - 1)


def max_drawdown(r: pd.Series) -> tuple[float, int]:
    """(max drawdown, longest underwater stretch in months)."""
    idx = np.cumprod(1 + np.asarray(r))
    peak = np.maximum.accumulate(idx)
    dd = idx / peak - 1
    under, longest = 0, 0
    for x in dd:
        under = under + 1 if x < -1e-12 else 0
        longest = max(longest, under)
    return float(dd.min()), longest


def stats(r: pd.Series, bills: pd.Series, infl: pd.Series) -> dict:
    bills = bills.reindex(r.index)
    infl = infl.reindex(r.index)
    ex = r - bills
    mdd, uw = max_drawdown(r)
    real = (1 + r) / (1 + infl) - 1
    rmdd, ruw = max_drawdown(real)
    return {
        "cagr": cagr(r),
        "real_cagr": cagr(real),
        "vol": float(r.std() * np.sqrt(12)),
        # undefined for (near) cash-like series
        "sharpe": float(ex.mean() / ex.std() * np.sqrt(12)) if ex.std() * np.sqrt(12) > 0.01 else float("nan"),
        "max_drawdown": mdd,
        "longest_underwater_months": uw,
        "real_max_drawdown": rmdd,
        "real_longest_underwater_months": ruw,
        "worst_12m": float((1 + r).rolling(12).apply(np.prod, raw=True).min() - 1),
        "excess_cagr_vs_bills": cagr(r) - cagr(bills),
    }


def rolling_loss_probs(r: pd.Series, bills: pd.Series, infl: pd.Series,
                       horizons=(12, 60, 120)) -> dict:
    """Share of (overlapping) historical windows with a loss. Overlapping
    windows are highly autocorrelated: effective sample size is roughly
    (years of data / horizon years), so treat long-horizon numbers as rough."""
    lr = np.log1p(r)
    lb = np.log1p(bills.reindex(r.index))
    li = np.log1p(infl.reindex(r.index))
    out = {}
    for h in horizons:
        a = lr.rolling(h).sum().dropna()
        b = lb.rolling(h).sum().dropna()
        i = li.rolling(h).sum().dropna()
        out[f"{h // 12}y"] = {
            "p_nominal_loss": float((a < 0).mean()),
            "p_real_loss": float((a - i < 0).mean()),
            "p_below_bills": float((a - b < 0).mean()),
            "worst_annualized": float(np.expm1(a.min() * 12 / h)),
            "median_annualized": float(np.expm1(a.median() * 12 / h)),
            "n_windows": int(len(a)),
            "n_independent": int(len(r) // h),
        }
    return out


def stationary_bootstrap_idx(n_obs: int, n_paths: int, length: int, mean_block: float,
                             rng: np.random.Generator) -> np.ndarray:
    """Politis-Romano stationary bootstrap indices, shape (n_paths, length)."""
    idx = np.empty((n_paths, length), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n_obs, n_paths)
    p = 1.0 / mean_block
    new = rng.random((n_paths, length)) < p
    rnd = rng.integers(0, n_obs, (n_paths, length))
    for t in range(1, length):
        idx[:, t] = np.where(new[:, t], rnd[:, t], (idx[:, t - 1] + 1) % n_obs)
    return idx


def bootstrap_loss_probs(data: pd.DataFrame, weights: dict[str, float], annual_drag: float,
                         horizons=(12, 60, 120), n_paths: int = 10_000, mean_block: float = 24,
                         stock_haircut: float = 0.0, seed: int = 7) -> dict:
    """Monte-Carlo loss probabilities. Joint monthly rows (stocks, bonds,
    bills, inflation) are resampled in blocks to keep cross-asset and
    serial dependence. Monthly-rebalanced fixed weights; costs/taxes enter as
    ``annual_drag`` (measured from the full engine backtest).
    ``stock_haircut``: annual return subtracted from equities to reflect
    lower forward-looking expectations than the 1926-2021 history."""
    rng = np.random.default_rng(seed)
    cols = {"stock": "stock_tr", "bond": "bond_tr", "bill": "bill_tr"}
    R = np.zeros(len(data))
    for a, w in weights.items():
        x = data[cols[a]].to_numpy().copy()
        if a == "stock":
            x = x - stock_haircut / 12
        R = R + w * x
    R = R - annual_drag / 12
    B = data["bill_tr"].to_numpy()
    I = data["infl"].to_numpy()
    H = max(horizons)
    idx = stationary_bootstrap_idx(len(data), n_paths, H, mean_block, rng)
    lr, lb, li = np.log1p(R[idx]).cumsum(1), np.log1p(B[idx]).cumsum(1), np.log1p(I[idx]).cumsum(1)
    out = {}
    for h in horizons:
        a, b, i = lr[:, h - 1], lb[:, h - 1], li[:, h - 1]
        out[f"{h // 12}y"] = {
            "p_nominal_loss": float((a < 0).mean()),
            "p_real_loss": float((a - i < 0).mean()),
            "p_below_bills": float((a - b < 0).mean()),
            "p05_annualized": float(np.expm1(np.percentile(a, 5) * 12 / h)),
            "median_annualized": float(np.expm1(np.median(a) * 12 / h)),
        }
    return out


def walk_forward(data: pd.DataFrame, base: StrategyConfig, candidates: list[StrategyConfig],
                 first_test_year: int = 1951, step_years: int = 10,
                 tax: TaxConfig | None = None, cost: CostConfig | None = None,
                 select_tax: TaxConfig | None = None) -> dict:
    """Expanding-window walk-forward. For each test block, pick the candidate
    with the best in-sample Sharpe on all data before the block (selection is
    done tax-free unless ``select_tax`` given), then run it out-of-sample.
    Returns chained OOS returns for the selected rule and for ``base``."""
    select_tax = select_tax or TaxConfig(enabled=False)
    tax = tax or TaxConfig(enabled=False)
    blocks = []
    y = first_test_year
    last = data.index[-1].year
    while y <= last:
        blocks.append((f"{y}-01", f"{min(y + step_years - 1, last)}-12"))
        y += step_years
    sel_r, base_r, chosen = [], [], []
    for s, e in blocks:
        train = data.loc[: str(int(s[:4]) - 1) + "-12"]
        # first test month = last train month, used only as the entry point
        test = data.loc[:e].iloc[len(train) - 1:]
        if len(test) < 13:
            continue
        best, best_sh = None, -np.inf
        for c in candidates:
            r = run_backtest(train, c, tax=select_tax, cost=cost).monthly_returns
            ex = r - train["bill_tr"].reindex(r.index)
            sh = ex.mean() / ex.std()
            if sh > best_sh:
                best, best_sh = c, sh
        chosen.append({"test": f"{s}..{e}", "chosen": best.name, "train_sharpe": float(best_sh * np.sqrt(12))})
        sel_r.append(run_backtest(test, best, tax=tax, cost=cost, signal_data=data.loc[:e]).monthly_returns)
        base_r.append(run_backtest(test, base, tax=tax, cost=cost).monthly_returns)
    return {"blocks": chosen, "selected": pd.concat(sel_r), "base": pd.concat(base_r)}
