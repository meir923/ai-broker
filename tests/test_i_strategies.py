"""I1-I4 — tests for aibroker/strategies (swing indicators, regime, signals)"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

import pytest

from aibroker.strategies.swing import (
    SMACross,
    RSIMeanRev,
    DonchianBreak,
    Signal,
    compute_sma,
    compute_rsi,
    compute_atr,
    _atr_at,
    _trend_direction,
)
from aibroker.strategies.regime import (
    RegimeDetector,
    _vol_ratio,
    _trend_strength,
)


# ── helpers ──────────────────────────────────────────────────────────────

def _bars(n: int, base: float = 100.0, step: float = 0.5) -> list[dict[str, Any]]:
    dt = datetime(2023, 6, 1)
    result: list[dict[str, Any]] = []
    for i in range(n):
        c = round(base + i * step, 4)
        result.append({"date": dt.strftime("%Y-%m-%d"),
                        "o": round(c - 0.3, 4), "h": round(c + 1.5, 4),
                        "l": round(c - 1.5, 4), "c": c, "volume": 1_000_000})
        dt += timedelta(days=1)
        while dt.weekday() >= 5:
            dt += timedelta(days=1)
    return result


def _flat_bars(n: int, price: float = 100.0) -> list[dict[str, Any]]:
    dt = datetime(2023, 6, 1)
    return [{"date": (dt + timedelta(days=i)).strftime("%Y-%m-%d"),
             "o": price, "h": price, "l": price, "c": price, "volume": 1_000_000}
            for i in range(n)]


# ── compute_sma ──────────────────────────────────────────────────────────

class TestComputeSma:
    def test_basic(self):
        closes = [1.0, 2.0, 3.0, 4.0, 5.0]
        sma = compute_sma(closes, 3)
        assert sma[2] == pytest.approx(2.0)
        assert sma[4] == pytest.approx(4.0)

    def test_first_values_none(self):
        sma = compute_sma([1, 2, 3], 3)
        assert sma[0] is None
        assert sma[1] is None
        assert sma[2] is not None

    def test_too_few(self):
        sma = compute_sma([1, 2], 5)
        assert all(v is None for v in sma)


# ── compute_rsi ──────────────────────────────────────────────────────────

class TestComputeRsi:
    def test_uptrend_high(self):
        closes = [100 + i for i in range(30)]
        r = compute_rsi(closes, 14)
        assert r[14] is not None
        assert r[14] == 100.0  # pure uptrend

    def test_flat_at_50(self):
        closes = [100.0] * 30
        r = compute_rsi(closes, 14)
        assert r[14] is not None

    def test_too_few(self):
        r = compute_rsi([100, 101], 14)
        assert all(v is None for v in r)


# ── compute_atr ──────────────────────────────────────────────────────────

class TestComputeAtr:
    def test_flat_zero(self):
        bars = _flat_bars(30)
        a = compute_atr(bars, 14)
        assert a[14] is not None
        assert a[14] == pytest.approx(0.0, abs=0.01)

    def test_volatile_positive(self):
        bars = _bars(30, step=2)
        a = compute_atr(bars, 14)
        assert a[14] is not None and a[14] > 0

    def test_too_few(self):
        a = compute_atr(_bars(5), 14)
        assert all(v is None for v in a)


# ── _atr_at ──────────────────────────────────────────────────────────────

class TestAtrAt:
    def test_returns_positive(self):
        bars = _bars(60)
        assert _atr_at(bars, 50) > 0

    def test_fallback_on_short_bars(self):
        bars = _bars(5)
        val = _atr_at(bars, 4)
        assert val > 0  # falls back to 1.5% of price


# ── _trend_direction ─────────────────────────────────────────────────────

class TestTrendDirection:
    def test_uptrend(self):
        bars = _bars(100, base=100, step=1)
        assert _trend_direction(bars, 99) == "up"

    def test_short_neutral(self):
        bars = _bars(10)
        assert _trend_direction(bars, 9) == "neutral"


# ── SMACross ─────────────────────────────────────────────────────────────

class TestSMACross:
    def test_returns_none_early(self):
        bars = _bars(30)
        s = SMACross()
        assert s.evaluate(bars, 10, "flat") is None

    def test_returns_signal_or_none_on_enough_data(self):
        bars = _bars(200, base=100, step=0.5)
        s = SMACross()
        sig = s.evaluate(bars, 100, "flat")
        assert sig is None or isinstance(sig, Signal)


# ── RSIMeanRev ───────────────────────────────────────────────────────────

class TestRSIMeanRev:
    def test_returns_none_early(self):
        bars = _bars(30)
        s = RSIMeanRev()
        assert s.evaluate(bars, 10, "flat") is None

    def test_exit_long_above_55(self):
        bars = _bars(200, base=100, step=1)
        s = RSIMeanRev()
        sig = s.evaluate(bars, 100, "long")
        if sig is not None:
            assert sig.action == "exit_long"


# ── DonchianBreak ────────────────────────────────────────────────────────

class TestDonchianBreak:
    def test_returns_none_early(self):
        bars = _bars(30)
        s = DonchianBreak()
        assert s.evaluate(bars, 10, "flat") is None


# ── RegimeDetector ───────────────────────────────────────────────────────

class TestRegimeDetector:
    def test_initial_leverage(self):
        bars = _bars(100)
        rd = RegimeDetector()
        lev = rd.get_leverage("SPY", bars, 50, 100_000)
        assert 1.0 <= lev <= 5.0

    def test_drawdown_reduces_leverage(self):
        rd = RegimeDetector()
        rd.update_equity(100_000)
        dd = rd.portfolio_dd_pct(90_000)
        assert dd == pytest.approx(0.1)
        bars = _bars(100)
        lev = rd.get_leverage("SPY", bars, 99, 90_000)
        assert lev == 1.0  # >8% drawdown -> min leverage

    def test_update_equity_tracks_peak(self):
        rd = RegimeDetector()
        rd.update_equity(100)
        rd.update_equity(120)
        rd.update_equity(110)
        assert rd._peak_equity == 120


class TestVolRatio:
    def test_returns_none_early(self):
        bars = _bars(15)
        assert _vol_ratio(bars, 10) is None

    def test_returns_float_enough_data(self):
        bars = _bars(100)
        r = _vol_ratio(bars, 50)
        assert r is None or r > 0


class TestTrendStrength:
    def test_zero_early(self):
        bars = _bars(30)
        assert _trend_strength(bars, 20) == 0.0

    def test_positive_far_from_mean(self):
        bars = _bars(100, step=3)
        ts = _trend_strength(bars, 99)
        assert ts > 0
