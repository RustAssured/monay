"""Strategy presets (allocation menu). The operator picks ONE based on risk
tolerance and horizon; none is "best" - they trade return for drawdown."""
from __future__ import annotations

from .config import StrategyConfig


def presets() -> dict[str, StrategyConfig]:
    return {
        "bills": StrategyConfig("bills", {"bill": 1.0}),
        "stocks100": StrategyConfig("stocks100", {"stock": 1.0}),
        "s80_b20": StrategyConfig("s80_b20", {"stock": 0.8, "bond": 0.2}),
        "s60_b40": StrategyConfig("s60_b40", {"stock": 0.6, "bond": 0.4}),
        "s40_b60": StrategyConfig("s40_b60", {"stock": 0.4, "bond": 0.6}),
        "s60_b40_tlh": StrategyConfig("s60_b40_tlh", {"stock": 0.6, "bond": 0.4}, tlh=True),
        "stocks100_tlh": StrategyConfig("stocks100_tlh", {"stock": 1.0}, tlh=True),
        "s60_b40_trend10": StrategyConfig("s60_b40_trend10", {"stock": 0.6, "bond": 0.4}, trend_sma=10),
    }
