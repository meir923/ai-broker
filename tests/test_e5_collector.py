"""E5 — tests for aibroker/agent/collector.py (indicators, snapshot)"""
from __future__ import annotations

import math

import pytest

from aibroker.data.historical import Bar
from aibroker.agent.collector import (
    atr,
    build_snapshot,
    market_clock,
    rsi,
    sma,
    technicals_for_symbol,
    trend_label,
)


def _bars(n: int, base: float = 100.0, step: float = 1.0) -> list[Bar]:
    from datetime import datetime, timedelta
    dt = datetime(2024, 1, 2)
    result: list[Bar] = []
    for i in range(n):
        c = round(base + i * step, 4)
        result.append(Bar(date=dt.strftime("%Y-%m-%d"), o=round(c - 0.5, 4),
                          h=round(c + 2, 4), l=round(c - 2, 4), c=c, volume=1_000_000))
        dt += timedelta(days=1)
    return result


def _flat_bars(n: int, price: float = 100.0) -> list[Bar]:
    from datetime import datetime, timedelta
    dt = datetime(2024, 1, 2)
    return [Bar(date=(dt + timedelta(days=i)).strftime("%Y-%m-%d"),
                o=price, h=price, l=price, c=price, volume=1_000_000) for i in range(n)]


# ── sma ──────────────────────────────────────────────────────────────────

class TestSma:
    def test_basic(self):
        bars = _bars(30, base=100, step=0)
        assert sma(bars, 29, 10) == pytest.approx(100.0)

    def test_too_few_bars(self):
        bars = _bars(5)
        assert sma(bars, 4, 10) is None

    def test_single_bar(self):
        bars = _bars(5)
        assert sma(bars, 0, 1) == pytest.approx(float(bars[0]["c"]))


# ── rsi ──────────────────────────────────────────────────────────────────

class TestRsi:
    def test_uptrend_high(self):
        bars = _bars(30, base=100, step=1)
        r = rsi(bars, 29, 14)
        assert r is not None and r > 60

    def test_flat_at_50(self):
        bars = _flat_bars(30)
        r = rsi(bars, 29, 14)
        assert r == pytest.approx(50.0)

    def test_too_few(self):
        bars = _bars(10)
        assert rsi(bars, 9, 14) is None


# ── atr ──────────────────────────────────────────────────────────────────

class TestAtr:
    def test_flat_zero(self):
        bars = _flat_bars(30)
        a = atr(bars, 29, 14)
        assert a is not None and a == pytest.approx(0.0, abs=0.01)

    def test_volatile_positive(self):
        bars = _bars(30, base=100, step=2)
        a = atr(bars, 29, 14)
        assert a is not None and a > 0

    def test_too_few(self):
        assert atr(_bars(10), 9, 14) is None


# ── trend_label ──────────────────────────────────────────────────────────

class TestTrendLabel:
    def test_uptrend(self):
        bars = _bars(60, base=100, step=1)
        assert trend_label(bars, 59) == "UP"

    def test_short_bars(self):
        bars = _bars(3)
        assert trend_label(bars, 2) in ("UP", "DOWN", "SIDEWAYS", "N/A")


# ── technicals_for_symbol ────────────────────────────────────────────────

class TestTechnicals:
    def test_keys_present(self):
        bars = _bars(50)
        t = technicals_for_symbol(bars, 49)
        assert set(t.keys()) == {"price", "ma20", "rsi14", "atr14", "trend", "volume"}

    def test_price_correct(self):
        bars = _bars(50, base=200)
        t = technicals_for_symbol(bars, 49)
        assert t["price"] == pytest.approx(float(bars[49]["c"]), abs=0.01)


# ── market_clock ─────────────────────────────────────────────────────────

class TestMarketClock:
    def test_returns_expected_keys(self):
        c = market_clock()
        assert "ny_time" in c and "il_time" in c and "status" in c

    def test_status_valid(self):
        c = market_clock()
        assert c["status"] in ("OPEN", "PRE_MARKET", "AFTER_HOURS", "CLOSED")


# ── build_snapshot ───────────────────────────────────────────────────────

class TestBuildSnapshot:
    def test_basic_snapshot(self):
        bars = _bars(100)
        s = build_snapshot(
            symbols=["SPY"],
            history={"SPY": bars},
            bar_index=50,
            positions={},
            cash=100000,
            initial_deposit=100000,
        )
        assert s["portfolio"]["cash"] == 100000
        assert s["portfolio"]["equity"] == 100000
        assert s["portfolio"]["pnl"] == 0
        assert "SPY" in s["technicals"]

    def test_with_positions(self):
        bars = _bars(100, base=500)
        s = build_snapshot(
            symbols=["SPY"],
            history={"SPY": bars},
            bar_index=50,
            positions={"SPY": {"qty": 10, "avg_cost": 490}},
            cash=95000,
            initial_deposit=100000,
        )
        expected_eq = 95000 + 10 * float(bars[50]["c"])
        assert s["portfolio"]["equity"] == pytest.approx(expected_eq, abs=1)
        assert len(s["portfolio"]["positions"]) == 1

    def test_short_position_pnl(self):
        bars = _bars(100, base=100, step=0)
        s = build_snapshot(
            symbols=["SPY"],
            history={"SPY": bars},
            bar_index=50,
            positions={"SPY": {"qty": -10, "avg_cost": 110}},
            cash=101100,
            initial_deposit=100000,
        )
        pos = s["portfolio"]["positions"][0]
        assert pos["qty"] == -10
        assert pos["unrealized_pnl"] > 0  # shorted at 110, now at 100

    def test_sim_date(self):
        bars = _bars(100)
        s = build_snapshot(
            symbols=["SPY"],
            history={"SPY": bars},
            bar_index=50,
            positions={},
            cash=100000,
            initial_deposit=100000,
            sim_date="2024-03-15",
        )
        assert s["date"] == "2024-03-15"

    def test_empty_symbols(self):
        s = build_snapshot(
            symbols=[],
            history={},
            bar_index=0,
            positions={},
            cash=100000,
            initial_deposit=100000,
        )
        assert s["technicals"] == {}
        assert s["portfolio"]["equity"] == 100000
