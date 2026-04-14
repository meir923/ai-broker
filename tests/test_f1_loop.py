"""F1 — exhaustive tests for aibroker/agent/loop.py (simulation accounting core)"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest

from aibroker.data.historical import Bar
from aibroker.agent.loop import AgentSession, _align_history_by_date


# ── helpers ──────────────────────────────────────────────────────────────

def _bars(n: int, base: float = 100.0, step: float = 1.0, start_date: str = "2024-01-02") -> list[Bar]:
    from datetime import datetime, timedelta as td
    dt = datetime.strptime(start_date, "%Y-%m-%d")
    result: list[Bar] = []
    for i in range(n):
        c = round(base + i * step, 4)
        o = round(c - 0.5, 4)
        result.append(Bar(date=dt.strftime("%Y-%m-%d"), o=o, h=round(c + 2, 4), l=round(c - 2, 4), c=c, volume=1_000_000))
        dt += td(days=1)
        while dt.weekday() >= 5:
            dt += td(days=1)
    return result


def _session(bars_map: dict[str, list[Bar]], deposit: float = 100_000.0, risk: str = "medium") -> AgentSession:
    """Create an AgentSession pre-loaded with history (no network)."""
    s = AgentSession(mode="sim", symbols=list(bars_map.keys()), deposit=deposit, risk_level=risk)
    s._history = bars_map
    s._bar_index = 50
    s.running = True
    s.cash = deposit
    return s


# ── _align_history_by_date ───────────────────────────────────────────────

class TestAlignHistoryByDate:
    def test_identical_dates_unchanged(self):
        bars_a = _bars(100)
        bars_b = _bars(100)
        aligned = _align_history_by_date({"A": bars_a, "B": bars_b})
        assert len(aligned["A"]) == len(aligned["B"])
        for i in range(len(aligned["A"])):
            assert aligned["A"][i]["date"] == aligned["B"][i]["date"]

    def test_mismatched_dates_aligned(self):
        bars_a = _bars(60, start_date="2024-01-02")
        bars_b = _bars(60, start_date="2024-01-10")  # starts later
        aligned = _align_history_by_date({"A": bars_a, "B": bars_b})
        for i in range(len(aligned["A"])):
            assert aligned["A"][i]["date"] == aligned["B"][i]["date"]

    def test_too_few_common_dates_returns_original(self):
        bars_a = _bars(40, start_date="2024-01-02")
        bars_b = _bars(40, start_date="2024-06-01")
        result = _align_history_by_date({"A": bars_a, "B": bars_b})
        assert result is not None

    def test_empty_history(self):
        assert _align_history_by_date({}) == {}

    def test_single_symbol(self):
        bars = _bars(100)
        aligned = _align_history_by_date({"SPY": bars})
        assert len(aligned["SPY"]) == 100


# ── equity ───────────────────────────────────────────────────────────────

class TestEquity:
    def test_no_positions(self):
        s = _session({"SPY": _bars(100)})
        assert s.equity() == pytest.approx(100_000.0)

    def test_long_position(self):
        s = _session({"SPY": _bars(100, base=100, step=1)})
        px = float(s._history["SPY"][50]["c"])
        s.positions = {"SPY": {"qty": 10, "avg_cost": 90.0}}
        expected = s.cash + 10 * px
        assert s.equity() == pytest.approx(expected)

    def test_short_position(self):
        s = _session({"SPY": _bars(100, base=100, step=1)})
        px = float(s._history["SPY"][50]["c"])
        s.cash = 120_000.0
        s.positions = {"SPY": {"qty": -10, "avg_cost": 110.0}}
        expected = 120_000.0 + (-10) * px
        assert s.equity() == pytest.approx(expected)


# ── _sim_buying_power (equity-based, no infinite leverage) ───────────────

class TestSimBuyingPower:
    def test_no_positions(self):
        s = _session({"SPY": _bars(100)})
        assert s._sim_buying_power() == pytest.approx(100_000.0)

    def test_long_reduces_bp(self):
        s = _session({"SPY": _bars(100, base=500)})
        px = float(s._history["SPY"][50]["c"])
        s.positions = {"SPY": {"qty": 100, "avg_cost": px}}
        s.cash = 100_000 - 100 * px
        eq = s.equity()
        margin = 100 * px * s._margin_rate()
        assert s._sim_buying_power() == pytest.approx(eq - margin)

    def test_short_does_NOT_inflate_bp(self):
        """The critical infinite-leverage test: shorting must NOT increase buying power."""
        s = _session({"SPY": _bars(100, base=100)})
        bp_before = s._sim_buying_power()

        # Simulate a short of 100 shares at $150
        px = float(s._history["SPY"][50]["c"])
        short_qty = 100
        s.cash += px * short_qty  # cash goes up (short proceeds)
        s.positions = {"SPY": {"qty": -short_qty, "avg_cost": px}}

        bp_after = s._sim_buying_power()
        assert bp_after <= bp_before, (
            f"Buying power INCREASED after short! Before={bp_before}, After={bp_after}. "
            "This indicates an infinite leverage loop."
        )

    def test_multiple_shorts_dont_compound_bp(self):
        s = _session({"SPY": _bars(100, base=100), "AAPL": _bars(100, base=200)})
        bp_initial = s._sim_buying_power()

        px_spy = float(s._history["SPY"][50]["c"])
        px_aapl = float(s._history["AAPL"][50]["c"])

        s.cash += px_spy * 50 + px_aapl * 30
        s.positions = {
            "SPY": {"qty": -50, "avg_cost": px_spy},
            "AAPL": {"qty": -30, "avg_cost": px_aapl},
        }
        bp_after = s._sim_buying_power()
        assert bp_after < bp_initial

    def test_bp_never_negative(self):
        s = _session({"SPY": _bars(100, base=500)})
        px = float(s._history["SPY"][50]["c"])
        s.positions = {"SPY": {"qty": 1000, "avg_cost": px}}
        s.cash = 0
        assert s._sim_buying_power() >= 0


# ── _next_bar_open (lookahead-free execution) ────────────────────────────

class TestNextBarOpen:
    def test_returns_next_open(self):
        bars = _bars(100, base=100, step=1)
        s = _session({"SPY": bars})
        s._bar_index = 50
        expected_open = float(bars[51]["o"])
        assert s._next_bar_open("SPY") == pytest.approx(expected_open)

    def test_last_bar_falls_back_to_close(self):
        bars = _bars(100)
        s = _session({"SPY": bars})
        s._bar_index = 99
        expected_close = float(bars[99]["c"])
        assert s._next_bar_open("SPY") == pytest.approx(expected_close)

    def test_unknown_symbol_returns_zero(self):
        s = _session({"SPY": _bars(100)})
        assert s._next_bar_open("ZZZZZ") == 0.0


# ── _execute_sim ─────────────────────────────────────────────────────────

class TestExecuteSim:
    def test_buy_reduces_cash(self):
        s = _session({"SPY": _bars(100, base=100, step=1)})
        fill_px = s._next_bar_open("SPY")
        s._execute_sim("SPY", "buy", 10, "test", "2024-03-01")
        assert s.cash == pytest.approx(100_000 - 10 * fill_px)
        assert s.positions["SPY"]["qty"] == 10

    def test_sell_increases_cash(self):
        s = _session({"SPY": _bars(100, base=100, step=1)})
        fill_px = s._next_bar_open("SPY")
        s.positions = {"SPY": {"qty": 20, "avg_cost": 100.0, "opened": "2024-01-01"}}
        s._execute_sim("SPY", "sell", 10, "take profit", "2024-03-01")
        assert s.cash == pytest.approx(100_000 + 10 * fill_px)
        assert s.positions["SPY"]["qty"] == 10

    def test_sell_all_removes_position(self):
        s = _session({"SPY": _bars(100, base=100, step=1)})
        s.positions = {"SPY": {"qty": 10, "avg_cost": 100.0, "opened": "2024-01-01"}}
        s._execute_sim("SPY", "sell", 10, "exit", "2024-03-01")
        assert "SPY" not in s.positions

    def test_short_adds_cash_creates_negative_position(self):
        s = _session({"SPY": _bars(100, base=100, step=1)})
        initial_cash = s.cash
        fill_px = s._next_bar_open("SPY")
        s._execute_sim("SPY", "short", 10, "test", "2024-03-01")
        assert s.cash > initial_cash
        assert s.positions["SPY"]["qty"] == -10

    def test_cover_reduces_short(self):
        s = _session({"SPY": _bars(100, base=100, step=1)})
        s.positions = {"SPY": {"qty": -20, "avg_cost": 150.0, "opened": "2024-01-01"}}
        s.cash = 120_000
        fill_px = s._next_bar_open("SPY")
        s._execute_sim("SPY", "cover", 10, "take profit", "2024-03-01")
        assert s.cash == pytest.approx(120_000 - 10 * fill_px)
        assert s.positions["SPY"]["qty"] == -10

    def test_cover_all_removes_position(self):
        s = _session({"SPY": _bars(100, base=100, step=1)})
        s.positions = {"SPY": {"qty": -10, "avg_cost": 150.0, "opened": "2024-01-01"}}
        s.cash = 120_000
        s._execute_sim("SPY", "cover", 10, "close", "2024-03-01")
        assert "SPY" not in s.positions

    def test_hold_does_nothing(self):
        s = _session({"SPY": _bars(100)})
        cash_before = s.cash
        s._execute_sim("SPY", "hold", 10, "wait", "2024-03-01")
        assert s.cash == cash_before
        assert s.positions == {}

    def test_zero_qty_does_nothing(self):
        s = _session({"SPY": _bars(100)})
        s._execute_sim("SPY", "buy", 0, "skip", "2024-03-01")
        assert s.positions == {}

    def test_negative_qty_does_nothing(self):
        s = _session({"SPY": _bars(100)})
        s._execute_sim("SPY", "buy", -5, "bad", "2024-03-01")
        assert s.positions == {}

    def test_sell_more_than_held_clips(self):
        s = _session({"SPY": _bars(100, base=100, step=1)})
        s.positions = {"SPY": {"qty": 5, "avg_cost": 100.0, "opened": "2024-01-01"}}
        s._execute_sim("SPY", "sell", 100, "over-sell", "2024-03-01")
        assert "SPY" not in s.positions  # sold all 5

    def test_cover_more_than_short_clips(self):
        s = _session({"SPY": _bars(100, base=100, step=1)})
        s.positions = {"SPY": {"qty": -5, "avg_cost": 150.0, "opened": "2024-01-01"}}
        s.cash = 120_000
        s._execute_sim("SPY", "cover", 100, "over-cover", "2024-03-01")
        assert "SPY" not in s.positions

    def test_buy_exceeding_bp_clips(self):
        s = _session({"SPY": _bars(100, base=100, step=1)})
        s.cash = 500  # very low cash
        fill_px = s._next_bar_open("SPY")
        s._execute_sim("SPY", "buy", 1000, "over-buy", "2024-03-01")
        max_possible = int(500 / fill_px)
        if max_possible > 0:
            assert s.positions["SPY"]["qty"] <= max_possible
        else:
            assert "SPY" not in s.positions

    def test_sell_on_empty_position_does_nothing(self):
        s = _session({"SPY": _bars(100)})
        s._execute_sim("SPY", "sell", 10, "no pos", "2024-03-01")
        assert s.positions == {}
        assert s.cash == 100_000

    def test_cover_on_long_does_nothing(self):
        s = _session({"SPY": _bars(100)})
        s.positions = {"SPY": {"qty": 10, "avg_cost": 100.0, "opened": "2024-01-01"}}
        s._execute_sim("SPY", "cover", 5, "wrong", "2024-03-01")
        assert s.positions["SPY"]["qty"] == 10

    def test_trade_logged(self):
        s = _session({"SPY": _bars(100)})
        s._execute_sim("SPY", "buy", 10, "entry", "2024-03-01")
        assert len(s.trades) == 1
        assert s.trades[0]["action"] == "buy"
        assert s.trades[0]["symbol"] == "SPY"

    def test_avg_cost_weighted(self):
        s = _session({"SPY": _bars(100, base=100, step=1)})
        px1 = s._next_bar_open("SPY")
        s._execute_sim("SPY", "buy", 10, "first", "2024-03-01")
        avg1 = s.positions["SPY"]["avg_cost"]
        assert avg1 == pytest.approx(px1, abs=0.01)

        s._bar_index = 60
        px2 = s._next_bar_open("SPY")
        s._execute_sim("SPY", "buy", 10, "second", "2024-03-11")
        avg2 = s.positions["SPY"]["avg_cost"]
        expected_avg = (10 * px1 + 10 * px2) / 20
        assert avg2 == pytest.approx(expected_avg, abs=0.01)


# ── conservation of equity (cash + positions = deposit) ──────────────────

class TestEquityConservation:
    def test_buy_sell_round_trip_conserves_equity(self):
        """Buy then sell at the same bar: equity should be conserved (minus fill spread)."""
        bars = _bars(100, base=100, step=0)  # flat prices
        s = _session({"SPY": bars})
        initial_eq = s.equity()
        s._execute_sim("SPY", "buy", 50, "entry", "2024-03-01")
        mid_eq = s.equity()
        s._execute_sim("SPY", "sell", 50, "exit", "2024-03-01")
        final_eq = s.equity()
        assert mid_eq == pytest.approx(initial_eq, rel=0.01)
        assert final_eq == pytest.approx(initial_eq, rel=0.01)

    def test_short_cover_round_trip_conserves_equity(self):
        bars = _bars(100, base=100, step=0)
        s = _session({"SPY": bars})
        initial_eq = s.equity()
        s._execute_sim("SPY", "short", 50, "entry", "2024-03-01")
        mid_eq = s.equity()
        s._execute_sim("SPY", "cover", 50, "exit", "2024-03-01")
        final_eq = s.equity()
        assert mid_eq == pytest.approx(initial_eq, rel=0.01)
        assert final_eq == pytest.approx(initial_eq, rel=0.01)


# ── _sim_risk_allows ─────────────────────────────────────────────────────

class TestSimRiskAllows:
    def test_blocks_when_drawdown_too_high(self):
        s = _session({"SPY": _bars(100, base=100)}, deposit=100_000, risk="medium")
        s.cash = 55_000  # equity = 55k, drawdown = 45% > 40% limit
        s.positions = {}
        assert s._sim_risk_allows("SPY", "buy", 10) is False

    def test_allows_within_drawdown_limit(self):
        s = _session({"SPY": _bars(100, base=100)}, deposit=100_000, risk="medium")
        s.cash = 70_000  # equity = 70k, drawdown = 30% < 40% limit
        assert s._sim_risk_allows("SPY", "buy", 10) is True

    def test_blocks_exposure_over_limit(self):
        s = _session({"SPY": _bars(100, base=100)}, deposit=100_000, risk="medium")
        # try to buy 400 shares at ~$150 = $60,000 = 60% > 35% limit
        assert s._sim_risk_allows("SPY", "buy", 400) is False

    def test_allows_small_exposure(self):
        s = _session({"SPY": _bars(100, base=100)}, deposit=100_000, risk="medium")
        assert s._sim_risk_allows("SPY", "buy", 10) is True

    def test_low_risk_tighter_drawdown(self):
        s = _session({"SPY": _bars(100, base=100)}, deposit=100_000, risk="low")
        s.cash = 72_000  # drawdown = 28% > 25% limit
        s.positions = {}
        assert s._sim_risk_allows("SPY", "buy", 10) is False

    def test_high_risk_wider_drawdown(self):
        s = _session({"SPY": _bars(100, base=100)}, deposit=100_000, risk="high")
        s.cash = 50_000  # drawdown = 50% < 55% limit
        s.positions = {}
        assert s._sim_risk_allows("SPY", "buy", 10) is True


class TestDequeMemoryBound:
    """Verify trades/decisions use bounded deque to prevent memory leak."""

    def test_trades_is_deque(self):
        from collections import deque
        s = _session({"SPY": _bars(60)})
        assert isinstance(s.trades, deque)
        assert s.trades.maxlen == 1000

    def test_decisions_is_deque(self):
        from collections import deque
        s = _session({"SPY": _bars(60)})
        assert isinstance(s.decisions, deque)
        assert s.decisions.maxlen == 1000

    def test_deque_drops_oldest(self):
        from collections import deque
        s = _session({"SPY": _bars(60)})
        for i in range(1100):
            s.trades.append({"step": i})
        assert len(s.trades) == 1000
        assert s.trades[0]["step"] == 100  # oldest 100 dropped
