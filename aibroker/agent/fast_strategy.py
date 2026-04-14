"""Vectorized momentum strategy for fast-forward simulation.

Precomputes ATR, RSI, SMA, and ROC arrays once for O(N) performance,
then each tick_fast step is O(num_symbols) lookups.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from aibroker.data.historical import Bar

log = logging.getLogger(__name__)


def _precompute_sma(closes: list[float], length: int) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < length or length < 1:
        return out
    running = sum(closes[:length])
    out[length - 1] = running / length
    for i in range(length, n):
        running += closes[i] - closes[i - length]
        out[i] = running / length
    return out


def _precompute_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_gain + avg_loss < 1e-12:
        out[period] = 50.0
    else:
        rs = avg_gain / max(avg_loss, 1e-12)
        out[period] = 100.0 - 100.0 / (1.0 + rs)

    for i in range(period + 1, n):
        d = closes[i] - closes[i - 1]
        g = d if d > 0 else 0.0
        l = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        if avg_gain + avg_loss < 1e-12:
            out[i] = 50.0
        else:
            rs = avg_gain / max(avg_loss, 1e-12)
            out[i] = round(100.0 - 100.0 / (1.0 + rs), 1)
    return out


def _precompute_atr(bars: list[Bar], period: int = 14) -> list[float | None]:
    n = len(bars)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    tr_sum = 0.0
    for i in range(1, period + 1):
        h, l, pc = float(bars[i]["h"]), float(bars[i]["l"]), float(bars[i - 1]["c"])
        tr_sum += max(h - l, abs(h - pc), abs(l - pc))
    out[period] = round(tr_sum / period, 4)

    atr_val = tr_sum / period
    for i in range(period + 1, n):
        h, l, pc = float(bars[i]["h"]), float(bars[i]["l"]), float(bars[i - 1]["c"])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        atr_val = (atr_val * (period - 1) + tr) / period
        out[i] = round(atr_val, 4)
    return out


class PrecomputedIndicators:
    """Holds precomputed indicator arrays for a single symbol."""

    __slots__ = ("closes", "highs", "lows", "sma20", "sma50", "sma200", "rsi14", "atr14")

    def __init__(self, bars: list[Bar]) -> None:
        self.closes = [float(b["c"]) for b in bars]
        self.highs = [float(b.get("h", b["c"])) for b in bars]
        self.lows = [float(b.get("l", b["c"])) for b in bars]
        self.sma20 = _precompute_sma(self.closes, 20)
        self.sma50 = _precompute_sma(self.closes, 50)
        self.sma200 = _precompute_sma(self.closes, 200)
        self.rsi14 = _precompute_rsi(self.closes, 14)
        self.atr14 = _precompute_atr(bars, 14)


def precompute_all(history: dict[str, list[Bar]]) -> dict[str, PrecomputedIndicators]:
    return {sym: PrecomputedIndicators(bars) for sym, bars in history.items()}


def rank_symbols(
    indicators: dict[str, PrecomputedIndicators],
    idx: int,
    symbols: list[str],
    momentum_weights: tuple[float, float, float],
) -> list[dict[str, Any]]:
    """Rank symbols by momentum score at bar index `idx`."""
    w10, w20, w50 = momentum_weights
    rankings: list[dict[str, Any]] = []

    for sym in symbols:
        ind = indicators.get(sym)
        if not ind or idx >= len(ind.closes) or idx < 50:
            continue
        price = ind.closes[idx]
        a14 = ind.atr14[idx]
        r14 = ind.rsi14[idx]
        ma20 = ind.sma20[idx]
        ma50 = ind.sma50[idx]
        if any(v is None for v in (a14, r14, ma20, ma50)) or price <= 0:
            continue

        roc10 = (price / ind.closes[idx - 10] - 1) * 100 if idx >= 10 and ind.closes[idx - 10] > 0 else 0
        roc20 = (price / ind.closes[idx - 20] - 1) * 100 if idx >= 20 and ind.closes[idx - 20] > 0 else 0
        roc50 = (price / ind.closes[idx - 50] - 1) * 100 if idx >= 50 and ind.closes[idx - 50] > 0 else roc20

        momentum = roc10 * w10 + roc20 * w20 + roc50 * w50

        rankings.append({
            "symbol": sym,
            "price": price,
            "bar_high": ind.highs[idx],
            "bar_low": ind.lows[idx],
            "momentum": round(momentum, 2),
            "roc10": round(roc10, 2),
            "roc20": round(roc20, 2),
            "roc50": round(roc50, 2),
            "rsi": r14,
            "atr": a14,
            "ma20": ma20,
            "ma50": ma50,
            "above_ma50": price > ma50,
        })

    return rankings


def detect_bear_regime(
    spy_indicators: PrecomputedIndicators | None,
    idx: int,
    bear_trigger: str,
) -> tuple[bool, float | None]:
    """Returns (is_bear, spy_rsi) based on SPY indicators."""
    if not spy_indicators or idx >= len(spy_indicators.closes):
        return False, None

    price = spy_indicators.closes[idx]
    ma200 = spy_indicators.sma200[idx]
    ma50 = spy_indicators.sma50[idx]
    rsi = spy_indicators.rsi14[idx]

    above_200 = price > ma200 if ma200 else True
    above_50 = price > ma50 if ma50 else True

    roc20 = 0.0
    if idx >= 20 and spy_indicators.closes[idx - 20] > 0:
        roc20 = (price / spy_indicators.closes[idx - 20] - 1) * 100

    if bear_trigger == "below_200":
        bear = not above_200
    else:
        bear = not above_200 and not above_50 and roc20 < -5

    return bear, rsi
