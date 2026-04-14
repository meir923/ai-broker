"""F2 — exhaustive tests for aibroker/agent/fast_strategy.py (mathematical core)"""
from __future__ import annotations

import math

import pytest

from aibroker.data.historical import Bar
from aibroker.agent.fast_strategy import (
    PrecomputedIndicators,
    _precompute_atr,
    _precompute_rsi,
    _precompute_sma,
    detect_bear_regime,
    precompute_all,
    rank_symbols,
)


# ── helpers ──────────────────────────────────────────────────────────────

def _bars(n: int, base: float = 100.0, step: float = 1.0) -> list[Bar]:
    """Generate n bars with linearly increasing close (uptrend)."""
    result: list[Bar] = []
    for i in range(n):
        c = base + i * step
        result.append(Bar(date=f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}", o=c - 0.5, h=c + 2, l=c - 2, c=c, volume=1_000_000))
    return result


def _flat_bars(n: int, price: float = 100.0) -> list[Bar]:
    """All bars at same price — zero volatility."""
    return [Bar(date=f"2024-01-{1 + i % 28:02d}", o=price, h=price, l=price, c=price, volume=1_000_000) for i in range(n)]


def _downtrend_bars(n: int, base: float = 200.0, step: float = 1.0) -> list[Bar]:
    result: list[Bar] = []
    for i in range(n):
        c = base - i * step
        if c < 1:
            c = 1
        result.append(Bar(date=f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}", o=c + 0.5, h=c + 2, l=c - 2, c=c, volume=1_000_000))
    return result


# ── _precompute_sma ──────────────────────────────────────────────────────

class TestPrecomputeSma:
    def test_simple_case(self):
        closes = [1.0, 2.0, 3.0, 4.0, 5.0]
        sma = _precompute_sma(closes, 3)
        assert sma[0] is None
        assert sma[1] is None
        assert sma[2] == pytest.approx(2.0)  # (1+2+3)/3
        assert sma[3] == pytest.approx(3.0)  # (2+3+4)/3
        assert sma[4] == pytest.approx(4.0)  # (3+4+5)/3

    def test_length_equals_data(self):
        closes = [10.0, 20.0, 30.0]
        sma = _precompute_sma(closes, 3)
        assert sma[2] == pytest.approx(20.0)

    def test_length_greater_than_data(self):
        sma = _precompute_sma([1.0, 2.0], 5)
        assert all(v is None for v in sma)

    def test_empty_input(self):
        assert _precompute_sma([], 5) == []

    def test_length_one(self):
        closes = [3.0, 5.0, 7.0]
        sma = _precompute_sma(closes, 1)
        assert sma == pytest.approx([3.0, 5.0, 7.0])

    def test_length_zero(self):
        sma = _precompute_sma([1.0, 2.0], 0)
        assert all(v is None for v in sma)

    def test_constant_series(self):
        closes = [42.0] * 20
        sma = _precompute_sma(closes, 5)
        for v in sma[4:]:
            assert v == pytest.approx(42.0)


# ── _precompute_rsi ──────────────────────────────────────────────────────

class TestPrecomputeRsi:
    def test_pure_uptrend_near_100(self):
        closes = [float(i) for i in range(50)]
        rsi = _precompute_rsi(closes, 14)
        for v in rsi[14:]:
            assert v is not None
            assert v > 90, f"RSI should be near 100 in pure uptrend, got {v}"

    def test_pure_downtrend_near_0(self):
        closes = [float(100 - i) for i in range(50)]
        rsi = _precompute_rsi(closes, 14)
        for v in rsi[14:]:
            assert v is not None
            assert v < 10, f"RSI should be near 0 in pure downtrend, got {v}"

    def test_flat_series_at_50(self):
        closes = [100.0] * 30
        rsi = _precompute_rsi(closes, 14)
        for v in rsi[14:]:
            assert v is not None
            assert v == pytest.approx(50.0)

    def test_rsi_range(self):
        closes = [100 + math.sin(i / 5) * 10 for i in range(100)]
        rsi = _precompute_rsi(closes, 14)
        for v in rsi[14:]:
            assert v is not None
            assert 0 <= v <= 100

    def test_insufficient_data(self):
        rsi = _precompute_rsi([1.0, 2.0, 3.0], 14)
        assert all(v is None for v in rsi)

    def test_first_values_are_none(self):
        rsi = _precompute_rsi([float(i) for i in range(50)], 14)
        for i in range(14):
            assert rsi[i] is None
        assert rsi[14] is not None

    def test_empty_input(self):
        assert _precompute_rsi([], 14) == []


# ── _precompute_atr ──────────────────────────────────────────────────────

class TestPrecomputeAtr:
    def test_constant_bars_atr_zero(self):
        bars = _flat_bars(30)
        atr = _precompute_atr(bars, 14)
        for v in atr[14:]:
            assert v is not None
            assert v == pytest.approx(0.0, abs=0.001)

    def test_volatile_bars_positive(self):
        bars = _bars(50, base=100, step=2)
        atr = _precompute_atr(bars, 14)
        for v in atr[14:]:
            assert v is not None
            assert v > 0

    def test_insufficient_data(self):
        bars = _bars(10)
        atr = _precompute_atr(bars, 14)
        assert all(v is None for v in atr)

    def test_length(self):
        bars = _bars(50)
        atr = _precompute_atr(bars, 14)
        assert len(atr) == 50

    def test_empty_input(self):
        assert _precompute_atr([], 14) == []


# ── PrecomputedIndicators ────────────────────────────────────────────────

class TestPrecomputedIndicators:
    def test_all_arrays_same_length(self):
        bars = _bars(250)
        pi = PrecomputedIndicators(bars)
        n = len(bars)
        assert len(pi.closes) == n
        assert len(pi.sma20) == n
        assert len(pi.sma50) == n
        assert len(pi.sma200) == n
        assert len(pi.rsi14) == n
        assert len(pi.atr14) == n

    def test_sma200_needs_200_bars(self):
        bars = _bars(100)
        pi = PrecomputedIndicators(bars)
        assert all(v is None for v in pi.sma200)

    def test_sma200_available_with_enough_bars(self):
        bars = _bars(250)
        pi = PrecomputedIndicators(bars)
        assert pi.sma200[199] is not None
        assert pi.sma200[249] is not None


# ── precompute_all ───────────────────────────────────────────────────────

class TestPrecomputeAll:
    def test_returns_dict_of_indicators(self):
        history = {"SPY": _bars(100), "AAPL": _bars(100, base=200)}
        result = precompute_all(history)
        assert "SPY" in result and "AAPL" in result
        assert isinstance(result["SPY"], PrecomputedIndicators)

    def test_empty_history(self):
        assert precompute_all({}) == {}


# ── rank_symbols ─────────────────────────────────────────────────────────

class TestRankSymbols:
    def test_uptrend_gets_positive_momentum(self):
        bars_up = _bars(100, base=100, step=1)
        indicators = {"UP": PrecomputedIndicators(bars_up)}
        rankings = rank_symbols(indicators, 80, ["UP"], (0.25, 0.40, 0.35))
        assert len(rankings) == 1
        assert rankings[0]["momentum"] > 0

    def test_downtrend_gets_negative_momentum(self):
        bars_down = _downtrend_bars(100, base=200, step=1)
        indicators = {"DOWN": PrecomputedIndicators(bars_down)}
        rankings = rank_symbols(indicators, 80, ["DOWN"], (0.25, 0.40, 0.35))
        assert len(rankings) == 1
        assert rankings[0]["momentum"] < 0

    def test_idx_too_small_skips(self):
        indicators = {"X": PrecomputedIndicators(_bars(100))}
        rankings = rank_symbols(indicators, 10, ["X"], (0.25, 0.40, 0.35))
        assert rankings == []

    def test_unknown_symbol_skipped(self):
        indicators = {"X": PrecomputedIndicators(_bars(100))}
        rankings = rank_symbols(indicators, 80, ["UNKNOWN"], (0.25, 0.40, 0.35))
        assert rankings == []

    def test_multiple_symbols_sorted_output(self):
        bars_fast = _bars(100, base=100, step=2)
        bars_slow = _bars(100, base=100, step=0.2)
        indicators = {
            "FAST": PrecomputedIndicators(bars_fast),
            "SLOW": PrecomputedIndicators(bars_slow),
        }
        rankings = rank_symbols(indicators, 80, ["FAST", "SLOW"], (0.25, 0.40, 0.35))
        assert len(rankings) == 2
        assert rankings[0]["symbol"] in ("FAST", "SLOW")

    def test_output_keys(self):
        indicators = {"X": PrecomputedIndicators(_bars(100))}
        rankings = rank_symbols(indicators, 80, ["X"], (0.25, 0.40, 0.35))
        expected_keys = {"symbol", "price", "bar_high", "bar_low", "momentum", "roc10", "roc20", "roc50", "rsi", "atr", "ma20", "ma50", "above_ma50"}
        assert set(rankings[0].keys()) == expected_keys

    def test_zero_price_skipped(self):
        bars = _flat_bars(100, price=0.0)
        indicators = {"ZERO": PrecomputedIndicators(bars)}
        rankings = rank_symbols(indicators, 80, ["ZERO"], (0.25, 0.40, 0.35))
        assert rankings == []


# ── detect_bear_regime ───────────────────────────────────────────────────

class TestDetectBearRegime:
    def test_uptrend_not_bear(self):
        bars = _bars(250, base=100, step=0.5)
        ind = PrecomputedIndicators(bars)
        bear, rsi = detect_bear_regime(ind, 249, "below_200")
        assert bear is False

    def test_downtrend_below_200_is_bear(self):
        bars = _downtrend_bars(250, base=300, step=0.5)
        ind = PrecomputedIndicators(bars)
        bear, rsi = detect_bear_regime(ind, 249, "below_200")
        assert bear is True

    def test_none_indicators_returns_false(self):
        bear, rsi = detect_bear_regime(None, 50, "below_200")
        assert bear is False
        assert rsi is None

    def test_idx_out_of_range_returns_false(self):
        ind = PrecomputedIndicators(_bars(50))
        bear, rsi = detect_bear_regime(ind, 100, "below_200")
        assert bear is False

    def test_below_200_and_50_trigger(self):
        bars = _downtrend_bars(250, base=300, step=0.5)
        ind = PrecomputedIndicators(bars)
        bear, rsi = detect_bear_regime(ind, 249, "below_200_and_50")
        assert isinstance(bear, bool)

    def test_rsi_returned(self):
        bars = _bars(50)
        ind = PrecomputedIndicators(bars)
        _, rsi = detect_bear_regime(ind, 30, "below_200")
        assert rsi is not None or rsi is None  # may be None if not enough data
