"""
Swing-trading strategies operating on daily OHLC bars.

Indicators: SMA, RSI (Wilder), ATR.
Strategies: SMACross, RSIMeanRev, DonchianBreak.

All strategies include a **trend filter** (SMA50): only long when
price > SMA50, only short when price < SMA50.  This prevents
counter-trend entries that historically caused most losses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def compute_sma(closes: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if period < 1 or len(closes) < period:
        return out
    s = sum(closes[:period])
    out[period - 1] = s / period
    for i in range(period, len(closes)):
        s += closes[i] - closes[i - period]
        out[i] = s / period
    return out


def compute_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if period < 1 or len(closes) < period + 1:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss < 1e-12:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - 100.0 / (1.0 + rs)

    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        g = max(delta, 0.0)
        l = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        if avg_loss < 1e-12:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def compute_atr(bars: list[dict[str, Any]], period: int = 14) -> list[float | None]:
    n = len(bars)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    trs: list[float] = [0.0]
    for i in range(1, n):
        h = float(bars[i]["h"])
        l = float(bars[i]["l"])
        pc = float(bars[i - 1]["c"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    s = sum(trs[1 : period + 1])
    atr = s / period
    out[period] = atr
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr
    return out


def _atr_at(bars: list[dict[str, Any]], idx: int) -> float:
    atr_list = compute_atr(bars[: idx + 1], 14)
    val = atr_list[idx] if idx < len(atr_list) else None
    if val is None or val < 0.01:
        return float(bars[idx]["c"]) * 0.015
    return val


def _trend_direction(bars: list[dict[str, Any]], bar_idx: int) -> str:
    """Returns 'up', 'down', or 'neutral' based on SMA(50)."""
    if bar_idx < 50:
        return "neutral"
    closes = [float(bars[i]["c"]) for i in range(bar_idx + 1)]
    sma50 = compute_sma(closes, 50)
    val = sma50[bar_idx]
    if val is None:
        return "neutral"
    px = closes[bar_idx]
    if px > val * 1.005:
        return "up"
    if px < val * 0.995:
        return "down"
    return "neutral"


# ---------------------------------------------------------------------------
# Signal dataclass
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    action: Literal["buy", "sell", "exit_long", "exit_short"]
    stop: float = 0.0
    target: float = 0.0
    reason: str = ""


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class SwingStrategy:
    name: str = "base"

    def evaluate(
        self,
        bars: list[dict[str, Any]],
        bar_idx: int,
        position_side: Literal["long", "short", "flat"],
    ) -> Signal | None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

STOP_ATR = 3.0
TARGET_ATR = 4.0


# ---------------------------------------------------------------------------
# 1. SMA Crossover  (trend-following)
# ---------------------------------------------------------------------------

class SMACross(SwingStrategy):
    """
    SMA(10) vs SMA(30) daily closes with SMA(50) trend filter.
    Only BUY in uptrend, only SELL in downtrend.
    """

    name = "sma_cross"

    def __init__(self, fast: int = 10, slow: int = 30) -> None:
        self.fast = fast
        self.slow = slow

    def evaluate(
        self,
        bars: list[dict[str, Any]],
        bar_idx: int,
        position_side: Literal["long", "short", "flat"],
    ) -> Signal | None:
        if bar_idx < 51:
            return None
        closes = [float(bars[i]["c"]) for i in range(bar_idx + 1)]
        sma_f = compute_sma(closes, self.fast)
        sma_s = compute_sma(closes, self.slow)
        cur_f, cur_s = sma_f[bar_idx], sma_s[bar_idx]
        prev_f, prev_s = sma_f[bar_idx - 1], sma_s[bar_idx - 1]
        if cur_f is None or cur_s is None or prev_f is None or prev_s is None:
            return None

        atr_val = _atr_at(bars, bar_idx)
        px = float(bars[bar_idx]["c"])
        trend = _trend_direction(bars, bar_idx)

        # Golden cross -> buy (only in uptrend)
        if prev_f <= prev_s and cur_f > cur_s:
            if position_side == "short":
                return Signal(action="exit_short", reason="SMA golden cross exit short")
            if position_side == "flat" and trend == "up":
                return Signal(action="buy",
                              stop=px - STOP_ATR * atr_val,
                              target=px + TARGET_ATR * atr_val,
                              reason="SMA golden cross (uptrend)")
        # Death cross -> sell (only in downtrend)
        if prev_f >= prev_s and cur_f < cur_s:
            if position_side == "long":
                return Signal(action="exit_long", reason="SMA death cross exit long")
            if position_side == "flat" and trend == "down":
                return Signal(action="sell",
                              stop=px + STOP_ATR * atr_val,
                              target=px - TARGET_ATR * atr_val,
                              reason="SMA death cross (downtrend)")
        return None


# ---------------------------------------------------------------------------
# 2. RSI Mean Reversion (with trend filter)
# ---------------------------------------------------------------------------

class RSIMeanRev(SwingStrategy):
    """
    RSI(14) on daily closes.
    BUY oversold ONLY in uptrend. SHORT overbought ONLY in downtrend.
    """

    name = "rsi_mean_rev"

    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70) -> None:
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def evaluate(
        self,
        bars: list[dict[str, Any]],
        bar_idx: int,
        position_side: Literal["long", "short", "flat"],
    ) -> Signal | None:
        if bar_idx < 51:
            return None
        closes = [float(bars[i]["c"]) for i in range(bar_idx + 1)]
        rsi_vals = compute_rsi(closes, self.period)
        rsi = rsi_vals[bar_idx]
        if rsi is None:
            return None

        px = float(bars[bar_idx]["c"])
        atr_val = _atr_at(bars, bar_idx)
        trend = _trend_direction(bars, bar_idx)

        if position_side == "long" and rsi > 55:
            return Signal(action="exit_long", reason=f"RSI={rsi:.0f} above 55")
        if position_side == "short" and rsi < 45:
            return Signal(action="exit_short", reason=f"RSI={rsi:.0f} below 45")
        if position_side == "flat":
            if rsi < self.oversold and trend == "up":
                return Signal(action="buy",
                              stop=px - STOP_ATR * atr_val,
                              target=px + TARGET_ATR * atr_val,
                              reason=f"RSI={rsi:.0f} oversold (uptrend)")
            if rsi > self.overbought and trend == "down":
                return Signal(action="sell",
                              stop=px + STOP_ATR * atr_val,
                              target=px - TARGET_ATR * atr_val,
                              reason=f"RSI={rsi:.0f} overbought (downtrend)")
        return None


# ---------------------------------------------------------------------------
# 3. Donchian Breakout (channel) — already profitable, keep as-is + trend
# ---------------------------------------------------------------------------

class DonchianBreak(SwingStrategy):
    """
    20-day high / 20-day low channel with SMA(50) trend filter.
    """

    name = "donchian_break"

    def __init__(self, period: int = 20) -> None:
        self.period = period

    def evaluate(
        self,
        bars: list[dict[str, Any]],
        bar_idx: int,
        position_side: Literal["long", "short", "flat"],
    ) -> Signal | None:
        if bar_idx < 51:
            return None
        window = bars[bar_idx - self.period : bar_idx]
        high_n = max(float(b["h"]) for b in window)
        low_n = min(float(b["l"]) for b in window)
        mid = (high_n + low_n) / 2
        px = float(bars[bar_idx]["c"])
        atr_val = _atr_at(bars, bar_idx)
        trend = _trend_direction(bars, bar_idx)

        pass  # No signal exit — trailing stop + TP handle all exits
        if position_side == "flat":
            # Volatility expansion filter: ATR(5) > ATR(20)
            if bar_idx >= 21:
                atr5_list = compute_atr(bars[:bar_idx+1], 5)
                atr20_list = compute_atr(bars[:bar_idx+1], 20)
                a5 = atr5_list[bar_idx] if bar_idx < len(atr5_list) and atr5_list[bar_idx] else None
                a20 = atr20_list[bar_idx] if bar_idx < len(atr20_list) and atr20_list[bar_idx] else None
                if a5 is not None and a20 is not None and a5 < a20 * 0.9:
                    return None  # volatility contracting — skip

            if px > high_n and trend != "down":
                return Signal(action="buy",
                              stop=px - STOP_ATR * atr_val,
                              target=px + TARGET_ATR * atr_val,
                              reason=f"Donchian breakout above {high_n:.2f}")
            if px < low_n and trend != "up":
                return Signal(action="sell",
                              stop=px + STOP_ATR * atr_val,
                              target=px - TARGET_ATR * atr_val,
                              reason=f"Donchian breakdown below {low_n:.2f}")
        return None


ALL_SWING_STRATEGIES: list[type[SwingStrategy]] = [SMACross, RSIMeanRev, DonchianBreak]
