from __future__ import annotations

from aibroker.planb.strategies.base import StrategyContext, StrategySignal


class MomentumStrategy:
    """Long-only: buy when N-day return > threshold; sell when < 0."""

    def __init__(self, lookback: int = 20, entry_threshold_pct: float = 2.0) -> None:
        self.lookback = max(2, int(lookback))
        self.entry_threshold_pct = float(entry_threshold_pct)
        self.name = f"momentum_{self.lookback}_{self.entry_threshold_pct}"

    def reset(self) -> None:
        pass

    def on_bar(self, ctx: StrategyContext) -> tuple[StrategySignal, str]:
        i = ctx.bar_index
        bars = ctx.bars
        if i < self.lookback:
            return StrategySignal.NONE, "warmup"
        c0 = float(bars[i]["c"])
        c_past = float(bars[i - self.lookback]["c"])
        ret_pct = (c0 / c_past - 1.0) * 100.0 if c_past else 0.0
        if ret_pct > self.entry_threshold_pct and ctx.position_shares <= 0:
            return StrategySignal.BUY, f"ret_{ret_pct:.2f}%"
        if ret_pct < 0 and ctx.position_shares > 0:
            return StrategySignal.SELL, f"ret_{ret_pct:.2f}%"
        return StrategySignal.NONE, ""
