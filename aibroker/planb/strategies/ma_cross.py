from __future__ import annotations

from aibroker.data.historical import Bar
from aibroker.planb.strategies.base import StrategyContext, StrategySignal


class MACrossStrategy:
    """Long-only MA crossover on daily close."""

    def __init__(self, fast: int = 10, slow: int = 30) -> None:
        self.fast = max(2, int(fast))
        self.slow = max(self.fast + 1, int(slow))
        self.name = f"ma_cross_{self.fast}_{self.slow}"

    def reset(self) -> None:
        pass

    def on_bar(self, ctx: StrategyContext) -> tuple[StrategySignal, str]:
        i = ctx.bar_index
        bars = ctx.bars
        if i < self.slow:
            return StrategySignal.NONE, "warmup"
        c = float(bars[i]["c"])
        def sma(length: int, end: int) -> float:
            s = sum(float(bars[j]["c"]) for j in range(end - length + 1, end + 1))
            return s / length

        f_now = sma(self.fast, i)
        s_now = sma(self.slow, i)
        f_prev = sma(self.fast, i - 1)
        s_prev = sma(self.slow, i - 1)
        cross_up = f_now > s_now and f_prev <= s_prev
        cross_dn = f_now < s_now and f_prev >= s_prev
        if cross_up and ctx.position_shares <= 0:
            return StrategySignal.BUY, "ma_cross_up"
        if cross_dn and ctx.position_shares > 0:
            return StrategySignal.SELL, "ma_cross_down"
        return StrategySignal.NONE, ""
