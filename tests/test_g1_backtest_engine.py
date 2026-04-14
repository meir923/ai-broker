"""G1 — tests for aibroker/planb/backtest/engine.py"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from aibroker.data.historical import Bar
from aibroker.planb.backtest.engine import (
    _apply_slippage,
    _fee_for_trade,
    _max_drawdown_pct,
    _sharpe_annualized,
    _update_daily_returns,
    run_backtest,
)
from aibroker.planb.config import PlanBCostsConfig, PlanBOOSConfig, PlanBRiskConfig
from aibroker.planb.strategies.base import Strategy, StrategyContext, StrategySignal


# ── helpers ──────────────────────────────────────────────────────────────

def _bars(n: int, base: float = 100.0, step: float = 0.5) -> list[Bar]:
    dt = datetime(2024, 1, 2)
    result: list[Bar] = []
    for i in range(n):
        c = round(base + i * step, 4)
        result.append(Bar(date=dt.strftime("%Y-%m-%d"), o=round(c - 0.3, 4),
                          h=round(c + 1, 4), l=round(c - 1, 4), c=c, volume=1_000_000))
        dt += timedelta(days=1)
        while dt.weekday() >= 5:
            dt += timedelta(days=1)
    return result


class AlwaysBuyStrategy(Strategy):
    name = "always_buy"
    def reset(self): pass
    def on_bar(self, ctx: StrategyContext):
        if ctx.position_shares <= 0:
            return StrategySignal.BUY, "buy"
        return StrategySignal.NONE, ""


class BuySellStrategy(Strategy):
    """Alternates buy/sell every 10 bars."""
    name = "buy_sell"
    def reset(self): self._step = 0
    def on_bar(self, ctx: StrategyContext):
        self._step += 1
        if self._step % 20 < 10 and ctx.position_shares <= 0:
            return StrategySignal.BUY, "entry"
        if self._step % 20 >= 10 and ctx.position_shares > 0:
            return StrategySignal.SELL, "exit"
        return StrategySignal.NONE, ""


# ── _fee_for_trade ───────────────────────────────────────────────────────

class TestFeeForTrade:
    def test_basic(self):
        costs = PlanBCostsConfig(fee_per_share_usd=0.01, fee_pct_of_notional=0.001, slippage_pct=0.0)
        fee = _fee_for_trade(costs, 100, 50.0, "buy")
        assert fee == pytest.approx(100 * 0.01 + 100 * 50 * 0.001)

    def test_zero_fees(self):
        costs = PlanBCostsConfig(fee_per_share_usd=0, fee_pct_of_notional=0, slippage_pct=0)
        assert _fee_for_trade(costs, 100, 50.0, "buy") == 0.0


# ── _apply_slippage ──────────────────────────────────────────────────────

class TestApplySlippage:
    def test_buy_increases_price(self):
        assert _apply_slippage(100, "buy", 0.01) == pytest.approx(101.0)

    def test_sell_decreases_price(self):
        assert _apply_slippage(100, "sell", 0.01) == pytest.approx(99.0)

    def test_zero_slippage(self):
        assert _apply_slippage(100, "buy", 0) == 100.0


# ── _max_drawdown_pct ────────────────────────────────────────────────────

class TestMaxDrawdown:
    def test_no_drawdown(self):
        assert _max_drawdown_pct([100, 110, 120]) == 0.0

    def test_simple_drawdown(self):
        dd = _max_drawdown_pct([100, 80, 90])
        assert dd == pytest.approx(20.0)

    def test_empty(self):
        assert _max_drawdown_pct([]) == 0.0

    def test_single_value(self):
        assert _max_drawdown_pct([100]) == 0.0

    def test_all_zeros(self):
        assert _max_drawdown_pct([0, 0, 0]) == 0.0

    def test_recovery(self):
        dd = _max_drawdown_pct([100, 50, 100])
        assert dd == pytest.approx(50.0)


# ── _sharpe_annualized ──────────────────────────────────────────────────

class TestSharpe:
    def test_too_few_returns(self):
        assert _sharpe_annualized([0.01, 0.02]) is None

    def test_zero_std(self):
        assert _sharpe_annualized([0.01] * 10) is None

    def test_positive_returns(self):
        rets = [0.01, 0.02, -0.005, 0.015, 0.008, 0.012]
        s = _sharpe_annualized(rets)
        assert s is not None and s > 0

    def test_negative_returns(self):
        rets = [-0.01, -0.02, -0.005, -0.015, -0.008, -0.012]
        s = _sharpe_annualized(rets)
        assert s is not None and s < 0


# ── _update_daily_returns ────────────────────────────────────────────────

class TestUpdateDailyReturns:
    def test_first_day_no_return(self):
        dl: dict[str, float] = {}
        dr: list[float] = []
        _update_daily_returns(dl, dr, "2024-01-02", 10000)
        assert dr == []
        assert dl["2024-01-02"] == 10000

    def test_second_day_records_return(self):
        dl = {"2024-01-02": 10000.0}
        dr: list[float] = []
        _update_daily_returns(dl, dr, "2024-01-03", 10100)
        assert len(dr) == 1
        assert dr[0] == pytest.approx(0.01)

    def test_same_day_updates_equity(self):
        dl = {"2024-01-02": 10000.0}
        dr: list[float] = []
        _update_daily_returns(dl, dr, "2024-01-02", 10200)
        assert dr == []  # same day, no new return
        assert dl["2024-01-02"] == 10200

    def test_empty_date_ignored(self):
        dl: dict[str, float] = {}
        dr: list[float] = []
        _update_daily_returns(dl, dr, "", 100)
        assert dl == {}


# ── run_backtest integration ─────────────────────────────────────────────

class TestRunBacktest:
    def _default_configs(self):
        costs = PlanBCostsConfig(fee_per_share_usd=0.0, fee_pct_of_notional=0.0, slippage_pct=0.0)
        risk = PlanBRiskConfig(max_daily_loss_usd=99999, max_trades_per_day=100,
                               max_notional_per_trade_usd=999999, kill_switch=False)
        oos = PlanBOOSConfig(mode="none", train_fraction=0.7)
        return costs, risk, oos

    def test_too_few_bars(self):
        costs, risk, oos = self._default_configs()
        result = run_backtest(_bars(10), AlwaysBuyStrategy(), symbol="SPY",
                              initial_cash_usd=10000, costs=costs, risk=risk, oos=oos)
        assert result.ok is False
        assert result.error == "not_enough_bars"

    def test_always_buy_uptrend_profits(self):
        costs, risk, oos = self._default_configs()
        bars = _bars(100, base=100, step=1)
        result = run_backtest(bars, AlwaysBuyStrategy(), symbol="SPY",
                              initial_cash_usd=10000, costs=costs, risk=risk, oos=oos)
        assert result.ok is True
        assert result.final_equity_usd > 10000
        assert result.return_pct_full > 0
        assert len(result.trades) >= 1

    def test_equity_conservation_with_zero_fees(self):
        """With zero fees/slippage in flat market, equity should be preserved."""
        costs, risk, oos = self._default_configs()
        bars = _bars(50, base=100, step=0)
        result = run_backtest(bars, BuySellStrategy(), symbol="SPY",
                              initial_cash_usd=10000, costs=costs, risk=risk, oos=oos)
        assert result.ok is True
        assert result.final_equity_usd == pytest.approx(10000, rel=0.001)

    def test_kill_switch_prevents_trades(self):
        costs, risk, oos = self._default_configs()
        bars = _bars(50, base=100, step=1)
        result = run_backtest(bars, AlwaysBuyStrategy(), symbol="SPY",
                              initial_cash_usd=10000, costs=costs, risk=risk, oos=oos,
                              kill_switch_active=True)
        assert result.ok is True
        assert len(result.trades) == 0
        assert result.final_equity_usd == pytest.approx(10000)

    def test_sharpe_populated_for_enough_data(self):
        costs, risk, oos = self._default_configs()
        bars = _bars(100, base=100, step=0.5)
        result = run_backtest(bars, BuySellStrategy(), symbol="SPY",
                              initial_cash_usd=10000, costs=costs, risk=risk, oos=oos)
        assert result.ok is True
        assert result.sharpe_annualized is not None

    def test_max_drawdown_populated(self):
        costs, risk, oos = self._default_configs()
        bars = _bars(100, base=200, step=-1)
        result = run_backtest(bars, AlwaysBuyStrategy(), symbol="SPY",
                              initial_cash_usd=10000, costs=costs, risk=risk, oos=oos)
        assert result.max_drawdown_pct >= 0
