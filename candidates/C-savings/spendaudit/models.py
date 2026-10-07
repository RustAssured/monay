"""Core data types. Sign convention: amount < 0 is money OUT, amount > 0 is money IN."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Transaction:
    date: date
    amount: float
    description: str
    account: str = "default"
    balance: Optional[float] = None   # running balance after this txn, if the bank provides it
    txid: str = ""                    # FITID for OFX, synthetic id for CSV
    source: str = ""                  # file / format it came from
    category_hint: str = ""           # bank-provided category, if any
    labels: dict = field(default_factory=dict)  # ground-truth labels (synthetic data only)

    @property
    def is_outflow(self) -> bool:
        return self.amount < 0


@dataclass
class Statement:
    transactions: list
    balances: dict = field(default_factory=dict)  # account -> (date, ledger balance)
    format: str = ""
