"""H2 — tests for paper_autopilot accounting (_apply_buy, _apply_sell, _equity)"""
from __future__ import annotations

import math

import pytest

from aibroker.simulation.paper_autopilot import (
    PaperSession,
    _apply_buy,
    _apply_sell,
    _calc_commission,
    _apply_slippage,
    _equity,
    _mark_prices,
)
from aibroker.data.historical import Bar


def _simple_session(cash: float = 100_000, lev: float = 2.0) -> PaperSession:
    bars = [Bar(date="2024-01-02", o=100, h=105, l=95, c=100, volume=1_000_000)]
    s = PaperSession(
        running=True,
        initial_deposit_usd=cash,
        cash_usd=cash,
        leverage=lev,
        history={"SPY": bars},
    )
    return s


class TestApplySlippage:
    def test_buy_higher(self):
        assert _apply_slippage(100, "buy") > 100

    def test_sell_lower(self):
        assert _apply_slippage(100, "sell") < 100


class TestCalcCommission:
    def test_minimum(self):
        assert _calc_commission(1) == 1.0  # min commission

    def test_large_qty(self):
        assert _calc_commission(1000) == pytest.approx(1000 * 0.005)


class TestApplyBuy:
    def test_basic_buy(self):
        s = _simple_session()
        ok = _apply_buy(s, "SPY", 10, 100)
        assert ok is True
        assert "SPY" in s.positions
        assert s.positions["SPY"]["qty"] > 0
        assert s.cash_usd < 100_000

    def test_zero_qty_rejected(self):
        s = _simple_session()
        assert _apply_buy(s, "SPY", 0, 100) is False

    def test_negative_qty_rejected(self):
        s = _simple_session()
        assert _apply_buy(s, "SPY", -5, 100) is False

    def test_zero_price_rejected(self):
        s = _simple_session()
        assert _apply_buy(s, "SPY", 10, 0) is False

    def test_nan_rejected(self):
        s = _simple_session()
        assert _apply_buy(s, "SPY", float("nan"), 100) is False
        assert _apply_buy(s, "SPY", 10, float("nan")) is False

    def test_insufficient_margin(self):
        s = _simple_session(cash=10)
        ok = _apply_buy(s, "SPY", 1000, 100)
        assert ok is False

    def test_cover_short(self):
        s = _simple_session(cash=50_000)
        _apply_sell(s, "SPY", 10, 100)
        assert s.positions["SPY"]["qty"] < 0
        ok = _apply_buy(s, "SPY", 10, 100)
        assert ok is True
        assert "SPY" not in s.positions  # fully covered


class TestApplySell:
    def test_basic_sell_short(self):
        s = _simple_session()
        ok = _apply_sell(s, "SPY", 10, 100)
        assert ok is True
        assert s.positions["SPY"]["qty"] < 0

    def test_close_long(self):
        s = _simple_session()
        _apply_buy(s, "SPY", 10, 100)
        initial_cash = s.cash_usd
        _apply_sell(s, "SPY", 10, 105)  # sell at profit
        assert "SPY" not in s.positions
        assert s.cash_usd > initial_cash

    def test_zero_qty_rejected(self):
        s = _simple_session()
        assert _apply_sell(s, "SPY", 0, 100) is False

    def test_insufficient_margin_for_short(self):
        s = _simple_session(cash=10)
        ok = _apply_sell(s, "SPY", 1000, 100)
        assert ok is False


class TestEquity:
    def test_no_positions(self):
        s = _simple_session(cash=50_000)
        mark = {"SPY": 100.0}
        assert _equity(s, mark) == pytest.approx(50_000)

    def test_long_position(self):
        s = _simple_session(cash=50_000, lev=2.0)
        _apply_buy(s, "SPY", 10, 100)
        mark = _mark_prices(s)
        eq = _equity(s, mark)
        assert eq > 0

    def test_equity_changes_with_price(self):
        s = _simple_session(cash=50_000, lev=2.0)
        _apply_buy(s, "SPY", 10, 100)
        eq_100 = _equity(s, {"SPY": 100.0})
        eq_110 = _equity(s, {"SPY": 110.0})
        assert eq_110 > eq_100  # long profits from price rise


class TestPaperSessionProperties:
    def test_win_rate_empty(self):
        s = _simple_session()
        assert s.win_rate == 0.0

    def test_avg_r_empty(self):
        s = _simple_session()
        assert s.avg_r == 0.0

    def test_days_simulated(self):
        s = _simple_session()
        s.bar_index = 60
        s.initial_bar_index = 50
        assert s.days_simulated == 10
