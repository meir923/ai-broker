"""C1 — exhaustive tests for aibroker/risk/gate.py"""
from __future__ import annotations

import pytest

from aibroker.brokers.base import OrderIntent
from aibroker.config.schema import AppConfig, RiskConfig
from aibroker.risk.gate import RiskDecision, evaluate_intent, _position_notional
from aibroker.state.runtime import RuntimeState


def _cfg(**risk_overrides) -> AppConfig:
    defaults = dict(max_daily_loss_usd=1000, max_notional_per_trade_usd=5000, max_trades_per_day=50)
    defaults.update(risk_overrides)
    return AppConfig(risk=defaults)


def _state(**kwargs) -> RuntimeState:
    return RuntimeState(**kwargs)


def _intent(symbol: str = "AAPL", side: str = "buy", qty: float = 10) -> OrderIntent:
    return OrderIntent(symbol=symbol, side=side, quantity=qty)


# ── kill switch ──────────────────────────────────────────────────────────

class TestKillSwitch:
    def test_config_kill_switch_blocks(self):
        r = evaluate_intent(_cfg(kill_switch=True), _state(), _intent())
        assert r.allowed is False
        assert "kill_switch" in r.reason

    def test_runtime_kill_switch_blocks(self):
        r = evaluate_intent(_cfg(), _state(kill_switch=True), _intent())
        assert r.allowed is False

    def test_both_kill_switches_off_allows(self):
        r = evaluate_intent(_cfg(), _state(), _intent())
        assert r.allowed is True


# ── empty symbol ─────────────────────────────────────────────────────────

class TestEmptySymbol:
    def test_empty_string_blocked(self):
        r = evaluate_intent(_cfg(), _state(), _intent(symbol=""))
        assert r.allowed is False
        assert "empty" in r.reason

    def test_whitespace_only_blocked(self):
        r = evaluate_intent(_cfg(), _state(), _intent(symbol="  "))
        assert r.allowed is False


# ── allowed symbols ──────────────────────────────────────────────────────

class TestAllowedSymbols:
    def test_symbol_not_in_list_blocked(self):
        r = evaluate_intent(
            _cfg(allowed_symbols=["SPY", "QQQ"]), _state(), _intent(symbol="TSLA"))
        assert r.allowed is False
        assert "not in allowed" in r.reason

    def test_symbol_in_list_allowed(self):
        r = evaluate_intent(
            _cfg(allowed_symbols=["AAPL"]), _state(), _intent(symbol="aapl"))
        assert r.allowed is True

    def test_empty_allowed_list_means_any(self):
        r = evaluate_intent(_cfg(allowed_symbols=[]), _state(), _intent(symbol="ZZZZZ"))
        assert r.allowed is True


# ── max trades per day ───────────────────────────────────────────────────

class TestMaxTradesPerDay:
    def test_at_limit_blocked(self):
        r = evaluate_intent(_cfg(max_trades_per_day=5), _state(trades_today=5), _intent())
        assert r.allowed is False
        assert "max_trades_per_day" in r.reason

    def test_below_limit_allowed(self):
        r = evaluate_intent(_cfg(max_trades_per_day=5), _state(trades_today=4), _intent())
        assert r.allowed is True

    def test_zero_limit_blocks_all(self):
        r = evaluate_intent(_cfg(max_trades_per_day=0), _state(trades_today=0), _intent())
        assert r.allowed is False


# ── daily loss ───────────────────────────────────────────────────────────

class TestDailyLoss:
    def test_loss_at_limit_blocked(self):
        r = evaluate_intent(_cfg(max_daily_loss_usd=500), _state(daily_pnl_usd=-500), _intent())
        assert r.allowed is False
        assert "max_daily_loss" in r.reason

    def test_loss_beyond_limit_blocked(self):
        r = evaluate_intent(_cfg(max_daily_loss_usd=500), _state(daily_pnl_usd=-600), _intent())
        assert r.allowed is False

    def test_loss_below_limit_allowed(self):
        r = evaluate_intent(_cfg(max_daily_loss_usd=500), _state(daily_pnl_usd=-499), _intent())
        assert r.allowed is True

    def test_positive_pnl_allowed(self):
        r = evaluate_intent(_cfg(max_daily_loss_usd=500), _state(daily_pnl_usd=1000), _intent())
        assert r.allowed is True

    def test_zero_pnl_allowed(self):
        r = evaluate_intent(_cfg(max_daily_loss_usd=500), _state(daily_pnl_usd=0), _intent())
        assert r.allowed is True


# ── notional per trade ───────────────────────────────────────────────────

class TestNotionalPerTrade:
    def test_over_max_blocked(self):
        r = evaluate_intent(
            _cfg(max_notional_per_trade_usd=5000), _state(),
            _intent(), estimated_notional_usd=5001)
        assert r.allowed is False
        assert "max_notional" in r.reason

    def test_at_max_allowed(self):
        r = evaluate_intent(
            _cfg(max_notional_per_trade_usd=5000), _state(),
            _intent(), estimated_notional_usd=5000)
        assert r.allowed is True

    def test_no_notional_skips_check(self):
        r = evaluate_intent(
            _cfg(max_notional_per_trade_usd=5000), _state(),
            _intent(), estimated_notional_usd=None)
        assert r.allowed is True


# ── position exposure ────────────────────────────────────────────────────

class TestPositionExposure:
    def test_exceeds_max_exposure_blocked(self):
        s = _state(
            equity_usd=100000,
            positions=[{"symbol": "AAPL", "qty": 50, "current_price": 200}],
        )
        r = evaluate_intent(
            _cfg(max_position_exposure_pct=25, max_notional_per_trade_usd=999999), s,
            _intent(symbol="AAPL"), estimated_notional_usd=20000)
        assert r.allowed is False
        assert "exposure" in r.reason

    def test_within_exposure_allowed(self):
        s = _state(equity_usd=100000, positions=[])
        r = evaluate_intent(
            _cfg(max_position_exposure_pct=25, max_notional_per_trade_usd=999999), s,
            _intent(symbol="AAPL"), estimated_notional_usd=20000)
        assert r.allowed is True

    def test_zero_equity_skips_check(self):
        s = _state(equity_usd=0, positions=[])
        r = evaluate_intent(
            _cfg(max_position_exposure_pct=25, max_notional_per_trade_usd=999999), s,
            _intent(), estimated_notional_usd=10000)
        assert r.allowed is True


# ── max open orders ──────────────────────────────────────────────────────

class TestMaxOpenOrders:
    def test_at_limit_blocked(self):
        s = _state(open_orders=[{"id": i} for i in range(20)])
        r = evaluate_intent(_cfg(max_open_orders=20), s, _intent())
        assert r.allowed is False
        assert "max_open_orders" in r.reason

    def test_below_limit_allowed(self):
        s = _state(open_orders=[{"id": 1}])
        r = evaluate_intent(_cfg(max_open_orders=20), s, _intent())
        assert r.allowed is True

    def test_zero_max_disables_check(self):
        s = _state(open_orders=[{"id": i} for i in range(100)])
        r = evaluate_intent(_cfg(max_open_orders=0), s, _intent())
        assert r.allowed is True


# ── _position_notional helper ────────────────────────────────────────────

class TestPositionNotional:
    def test_existing_position(self):
        s = _state(positions=[{"symbol": "AAPL", "qty": 10, "current_price": 200}])
        assert _position_notional(s, "AAPL") == 2000.0

    def test_short_position_uses_abs(self):
        s = _state(positions=[{"symbol": "SPY", "qty": -5, "current_price": 500}])
        assert _position_notional(s, "SPY") == 2500.0

    def test_no_matching_position(self):
        s = _state(positions=[{"symbol": "AAPL", "qty": 10, "current_price": 200}])
        assert _position_notional(s, "MSFT") == 0.0

    def test_empty_positions(self):
        assert _position_notional(_state(), "AAPL") == 0.0

    def test_fallback_to_avg_entry_price(self):
        s = _state(positions=[{"symbol": "AAPL", "qty": 10, "avg_entry_price": 150}])
        assert _position_notional(s, "AAPL") == 1500.0

    def test_case_insensitive_match(self):
        s = _state(positions=[{"symbol": "aapl", "qty": 10, "current_price": 200}])
        assert _position_notional(s, "AAPL") == 2000.0


# ── full pass-through (happy path) ──────────────────────────────────────

class TestHappyPath:
    def test_all_checks_pass(self):
        r = evaluate_intent(
            _cfg(max_daily_loss_usd=1000, max_notional_per_trade_usd=50000,
                 max_trades_per_day=100, max_position_exposure_pct=50, max_open_orders=50),
            _state(daily_pnl_usd=0, trades_today=0, equity_usd=100000),
            _intent(symbol="SPY", qty=10),
            estimated_notional_usd=5000,
        )
        assert r.allowed is True
        assert r.reason == "ok"
