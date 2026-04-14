from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from aibroker.data.historical import Bar


class StrategySignal(str, Enum):
    NONE = "none"
    BUY = "buy"
    SELL = "sell"


@dataclass
class StrategyContext:
    bar_index: int
    bars: list[Bar]
    position_shares: float
    cash_usd: float


class Strategy(Protocol):
    name: str

    def reset(self) -> None: ...

    def on_bar(self, ctx: StrategyContext) -> tuple[StrategySignal, str]:
        """Return signal and short reason for logging."""
        ...
