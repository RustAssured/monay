"""Configuration objects. Every number here is an ASSUMPTION the operator
should verify against their own broker / tax situation before going live."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Instrument:
    ticker: str
    asset: str            # asset class key: "stock" | "bond" | "bill"
    expense_ratio: float  # annual, e.g. 0.0003 = 0.03 %
    half_spread_bps: float


# Example instruments (illustrative, NOT recommendations). Expense ratios are
# issuer-published figures as remembered at time of writing; verify before use.
DEFAULT_INSTRUMENTS: dict[str, Instrument] = {
    "VTI": Instrument("VTI", "stock", 0.0003, 1.0),
    "ITOT": Instrument("ITOT", "stock", 0.0003, 1.0),     # TLH partner of VTI
    "IEF": Instrument("IEF", "bond", 0.0015, 1.0),
    "VGIT": Instrument("VGIT", "bond", 0.0004, 1.5),      # TLH partner of IEF
    "SGOV": Instrument("SGOV", "bill", 0.0009, 0.5),
    "BIL": Instrument("BIL", "bill", 0.00136, 0.5),
}

# Ordered preference per asset class: primary first, then TLH substitute.
DEFAULT_TICKERS: dict[str, list[str]] = {
    "stock": ["VTI", "ITOT"],
    "bond": ["IEF", "VGIT"],
    "bill": ["SGOV", "BIL"],
}


@dataclass
class CostConfig:
    commission_per_order: float = 0.0   # most large US brokers: $0 for ETFs (assumption)
    slippage_bps: float = 1.0           # extra market-impact/timing cost per trade
    spread_multiplier: float = 1.0      # stress multiplier for half-spreads


@dataclass
class TaxConfig:
    enabled: bool = True                # False = tax-advantaged account (IRA/401k)
    qualified_div_rate: float = 0.15
    ordinary_rate: float = 0.24         # interest, non-qualified income, ST gains
    ltcg_rate: float = 0.15
    stcg_rate: float = 0.24
    state_rate: float = 0.0             # applied to everything except T-bill interest
    ordinary_loss_offset: float = 3000.0
    qualified_div_fraction: float = 0.9  # share of equity dividends that are qualified


@dataclass
class StrategyConfig:
    name: str
    targets: dict[str, float]               # asset -> weight; must sum to 1
    band_abs: float = 0.05                  # 5/25 threshold rule
    band_rel: float = 0.25
    calendar_months: int | None = None      # e.g. 12 -> also rebalance every N months
    tlh: bool = False
    tlh_threshold: float = 0.05             # harvest a lot when it is >=5 % under basis
    tlh_min_loss: float = 100.0             # and the loss is >= $100
    trend_sma: int | None = None            # months; move stock sleeve to bills if TR index < SMA
    min_trade_value: float = 25.0
    cash_buffer: float = 0.0                # fraction kept in raw cash
    fractional: bool = True
    tickers: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_TICKERS))

    def __post_init__(self) -> None:
        s = sum(self.targets.values())
        if abs(s - 1.0) > 1e-9:
            raise ValueError(f"targets must sum to 1, got {s}")
        if any(w < 0 for w in self.targets.values()):
            raise ValueError("negative target weight")
