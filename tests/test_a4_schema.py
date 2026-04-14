"""A4 — exhaustive tests for aibroker/config/schema.py"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from aibroker.config.schema import (
    AppConfig,
    ExecutionConfig,
    GrokConfig,
    RiskConfig,
    StrategyConfig,
)


# ── RiskConfig ───────────────────────────────────────────────────────────

class TestRiskConfig:
    def test_valid_minimal(self):
        rc = RiskConfig(max_daily_loss_usd=500, max_notional_per_trade_usd=1000, max_trades_per_day=10)
        assert rc.max_daily_loss_usd == 500
        assert rc.max_position_exposure_pct == 25.0
        assert rc.max_open_orders == 20

    def test_zero_daily_loss_rejected(self):
        with pytest.raises(ValidationError):
            RiskConfig(max_daily_loss_usd=0, max_notional_per_trade_usd=100, max_trades_per_day=5)

    def test_negative_daily_loss_rejected(self):
        with pytest.raises(ValidationError):
            RiskConfig(max_daily_loss_usd=-100, max_notional_per_trade_usd=100, max_trades_per_day=5)

    def test_zero_trades_allowed(self):
        rc = RiskConfig(max_daily_loss_usd=500, max_notional_per_trade_usd=1000, max_trades_per_day=0)
        assert rc.max_trades_per_day == 0

    def test_kill_switch_default_off(self):
        rc = RiskConfig(max_daily_loss_usd=500, max_notional_per_trade_usd=1000, max_trades_per_day=10)
        assert rc.kill_switch is False

    def test_kill_switch_on(self):
        rc = RiskConfig(max_daily_loss_usd=500, max_notional_per_trade_usd=1000,
                        max_trades_per_day=10, kill_switch=True)
        assert rc.kill_switch is True

    def test_symbols_uppercase(self):
        rc = RiskConfig(max_daily_loss_usd=500, max_notional_per_trade_usd=1000,
                        max_trades_per_day=10, allowed_symbols=["aapl", " msft "])
        assert rc.allowed_symbols == ["AAPL", "MSFT"]

    def test_empty_symbols(self):
        rc = RiskConfig(max_daily_loss_usd=500, max_notional_per_trade_usd=1000,
                        max_trades_per_day=10, allowed_symbols=[])
        assert rc.allowed_symbols == []

    def test_exposure_pct_bounds(self):
        rc = RiskConfig(max_daily_loss_usd=500, max_notional_per_trade_usd=1000,
                        max_trades_per_day=10, max_position_exposure_pct=0)
        assert rc.max_position_exposure_pct == 0

        rc2 = RiskConfig(max_daily_loss_usd=500, max_notional_per_trade_usd=1000,
                         max_trades_per_day=10, max_position_exposure_pct=100)
        assert rc2.max_position_exposure_pct == 100

    def test_exposure_pct_over_100_rejected(self):
        with pytest.raises(ValidationError):
            RiskConfig(max_daily_loss_usd=500, max_notional_per_trade_usd=1000,
                       max_trades_per_day=10, max_position_exposure_pct=101)

    def test_exposure_pct_negative_rejected(self):
        with pytest.raises(ValidationError):
            RiskConfig(max_daily_loss_usd=500, max_notional_per_trade_usd=1000,
                       max_trades_per_day=10, max_position_exposure_pct=-1)

    def test_max_open_orders_zero_allowed(self):
        rc = RiskConfig(max_daily_loss_usd=500, max_notional_per_trade_usd=1000,
                        max_trades_per_day=10, max_open_orders=0)
        assert rc.max_open_orders == 0

    def test_max_open_orders_negative_rejected(self):
        with pytest.raises(ValidationError):
            RiskConfig(max_daily_loss_usd=500, max_notional_per_trade_usd=1000,
                       max_trades_per_day=10, max_open_orders=-1)


# ── ExecutionConfig ──────────────────────────────────────────────────────

class TestExecutionConfig:
    def test_default_dry_run(self):
        assert ExecutionConfig().dry_run is True

    def test_set_live(self):
        assert ExecutionConfig(dry_run=False).dry_run is False


# ── GrokConfig ───────────────────────────────────────────────────────────

class TestGrokConfig:
    def test_defaults(self):
        g = GrokConfig()
        assert g.enabled is False
        assert g.role == "off"
        assert g.orders.approval == "manual"
        assert g.chat.enabled is False

    def test_enable_with_role(self):
        g = GrokConfig(enabled=True, role="news_only")
        assert g.role == "news_only"

    def test_invalid_role_rejected(self):
        with pytest.raises(ValidationError):
            GrokConfig(role="magic")


# ── StrategyConfig ───────────────────────────────────────────────────────

class TestStrategyConfig:
    def test_default_rules(self):
        assert StrategyConfig().mode == "rules"

    def test_valid_modes(self):
        for m in ("rules", "rules_with_news_filter", "merge_grok_proposals"):
            assert StrategyConfig(mode=m).mode == m

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValidationError):
            StrategyConfig(mode="ai_magic")


# ── AppConfig ────────────────────────────────────────────────────────────

class TestAppConfig:
    def test_defaults(self):
        # risk fields are required — must supply them
        ac = AppConfig(risk={"max_daily_loss_usd": 500, "max_notional_per_trade_usd": 1000,
                             "max_trades_per_day": 10})
        assert ac.broker == "ibkr"
        assert ac.account_mode == "paper"
        assert ac.execution.dry_run is True

    def test_alpaca_broker(self):
        ac = AppConfig(broker="alpaca", risk={"max_daily_loss_usd": 500,
                       "max_notional_per_trade_usd": 1000, "max_trades_per_day": 10})
        assert ac.broker == "alpaca"

    def test_invalid_broker_rejected(self):
        with pytest.raises(ValidationError):
            AppConfig(broker="robinhood", risk={"max_daily_loss_usd": 500,
                      "max_notional_per_trade_usd": 1000, "max_trades_per_day": 10})

    def test_colmex_validator_no_crash(self):
        ac = AppConfig(
            broker="alpaca",
            signals={"colmex": "notify_only"},
            risk={"max_daily_loss_usd": 500, "max_notional_per_trade_usd": 1000, "max_trades_per_day": 10},
        )
        assert ac.signals.colmex == "notify_only"

    def test_missing_risk_fails(self):
        with pytest.raises(ValidationError):
            AppConfig(risk={})
