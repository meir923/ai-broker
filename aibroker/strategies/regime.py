"""
Dynamic leverage via market-regime detection.

Uses three signals per symbol:
  1. Volatility ratio  ATR(5) / ATR(20)
  2. Trend strength    abs(price - SMA50) / SMA50
  3. Portfolio drawdown (equity vs recent peak)

Returns a leverage multiplier in [1.0, max_leverage] that is
plugged into position sizing and margin calculations.
"""

from __future__ import annotations

from typing import Any

from aibroker.strategies.swing import compute_atr, compute_sma

MAX_LEVERAGE = 5.0
MIN_LEVERAGE = 1.0


def _vol_ratio(bars: list[dict[str, Any]], idx: int) -> float | None:
    """ATR(5) / ATR(20) — measures whether volatility is expanding or contracting."""
    if idx < 21:
        return None
    a5_list = compute_atr(bars[: idx + 1], 5)
    a20_list = compute_atr(bars[: idx + 1], 20)
    a5 = a5_list[idx] if idx < len(a5_list) else None
    a20 = a20_list[idx] if idx < len(a20_list) else None
    if a5 is None or a20 is None or a20 < 1e-9:
        return None
    return a5 / a20


def _trend_strength(bars: list[dict[str, Any]], idx: int) -> float:
    """abs(price - SMA50) / SMA50 — how far price is from the mean."""
    if idx < 50:
        return 0.0
    closes = [float(bars[i]["c"]) for i in range(idx + 1)]
    sma = compute_sma(closes, 50)
    val = sma[idx]
    if val is None or val < 1e-9:
        return 0.0
    return abs(closes[idx] - val) / val


class RegimeDetector:
    """Stateless helper — call ``get_leverage`` each bar per symbol."""

    def __init__(self, max_leverage: float = MAX_LEVERAGE) -> None:
        self.max_lev = min(max_leverage, 10.0)
        self._peak_equity: float = 0.0

    def update_equity(self, equity: float) -> None:
        if equity > self._peak_equity:
            self._peak_equity = equity

    def portfolio_dd_pct(self, equity: float) -> float:
        if self._peak_equity < 1e-6:
            return 0.0
        return max(0.0, (self._peak_equity - equity) / self._peak_equity)

    def get_leverage(
        self,
        sym: str,
        bars: list[dict[str, Any]],
        bar_idx: int,
        equity: float,
    ) -> float:
        self.update_equity(equity)

        dd = self.portfolio_dd_pct(equity)
        if dd > 0.08:
            return MIN_LEVERAGE

        vr = _vol_ratio(bars, bar_idx)
        ts = _trend_strength(bars, bar_idx)

        if vr is None:
            return 1.5

        if ts > 0.03 and vr < 0.9:
            lev = 2.5
        elif ts > 0.015 and vr < 1.1:
            lev = 2.0
        elif ts < 0.01 or vr > 1.2:
            lev = 1.0
        else:
            lev = 1.5

        return min(lev, self.max_lev)
