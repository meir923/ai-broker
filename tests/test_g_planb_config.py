"""G2-G13 — tests for planb config, strategies base, risk_state"""
from __future__ import annotations

import pytest

from aibroker.planb.config import (
    PlanBConfig,
    PlanBCostsConfig,
    PlanBRiskConfig,
    PlanBOOSConfig,
    PlanBDataConfig,
    PlanBLLMConfig,
    plan_b_config_to_public_dict,
    load_plan_b_config,
)
from aibroker.planb.strategies.base import StrategySignal, StrategyContext, Strategy
from aibroker.planb.risk_state import (
    set_runtime_kill_switch,
    runtime_kill_switch_active,
)


class TestPlanBCostsConfig:
    def test_defaults(self):
        c = PlanBCostsConfig()
        assert c.fee_per_share_usd == 0.005
        assert c.slippage_pct == 0.0005

    def test_zero_fees(self):
        c = PlanBCostsConfig(fee_per_share_usd=0, fee_pct_of_notional=0, slippage_pct=0)
        assert c.fee_per_share_usd == 0


class TestPlanBRiskConfig:
    def test_defaults(self):
        r = PlanBRiskConfig()
        assert r.kill_switch is False
        assert r.max_trades_per_day == 50

    def test_symbols_uppercased(self):
        r = PlanBRiskConfig(allowed_symbols=["spy", "qqq"])
        assert r.allowed_symbols == ["SPY", "QQQ"]


class TestPlanBOOSConfig:
    def test_defaults(self):
        o = PlanBOOSConfig()
        assert o.mode == "holdout_end"
        assert o.train_fraction == 0.7


class TestPlanBConfig:
    def test_defaults(self):
        c = PlanBConfig()
        assert c.market == "US"
        assert c.llm.enabled is False
        assert c.live.enabled is False

    def test_public_dict(self):
        c = PlanBConfig()
        d = plan_b_config_to_public_dict(c)
        assert "profile_name" in d
        assert "allowed_symbols" in d
        assert "costs" in d

    def test_load_missing_file(self):
        c = load_plan_b_config("/nonexistent/path.yaml")
        assert isinstance(c, PlanBConfig)  # returns defaults


class TestStrategyBase:
    def test_signal_values(self):
        assert StrategySignal.NONE == "none"
        assert StrategySignal.BUY == "buy"
        assert StrategySignal.SELL == "sell"

    def test_context_creation(self):
        ctx = StrategyContext(bar_index=10, bars=[], position_shares=0, cash_usd=100000)
        assert ctx.bar_index == 10
        assert ctx.cash_usd == 100000


class TestRiskState:
    def test_default_off(self):
        set_runtime_kill_switch(False)
        assert runtime_kill_switch_active() is False

    def test_activate(self):
        set_runtime_kill_switch(True)
        assert runtime_kill_switch_active() is True
        set_runtime_kill_switch(False)  # cleanup

    def test_toggle(self):
        set_runtime_kill_switch(True)
        assert runtime_kill_switch_active() is True
        set_runtime_kill_switch(False)
        assert runtime_kill_switch_active() is False
