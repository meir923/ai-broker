"""Tests for aibroker/agent/meta_policy.py — shared meta-policy logic."""
from __future__ import annotations

import pytest
from aibroker.agent.meta_policy import (
    PolicyContext, build_policy_context, apply_directional_policy,
    adjust_quantity, enforce_cash_floor, compute_cash_floor,
)


def _ctx(**overrides) -> PolicyContext:
    defaults = dict(
        avoid_set=frozenset(), priority_set=frozenset(),
        agg_mult=1.0, cash_bias="hold", cash_target_pct=10.0,
        exposure_bias="neutral",
    )
    defaults.update(overrides)
    return PolicyContext(**defaults)


class TestDirectionalPolicy:
    def test_avoid_symbol_blocks(self):
        ctx = _ctx(avoid_set=frozenset({"TSLA"}))
        r = apply_directional_policy("buy", "TSLA", ctx)
        assert not r.allowed
        assert "avoid_symbols" in r.reason

    def test_non_avoided_passes(self):
        ctx = _ctx(avoid_set=frozenset({"TSLA"}))
        r = apply_directional_policy("buy", "AAPL", ctx)
        assert r.allowed

    def test_mostly_cash_blocks_buy(self):
        ctx = _ctx(exposure_bias="mostly_cash")
        assert not apply_directional_policy("buy", "SPY", ctx).allowed

    def test_mostly_cash_blocks_short(self):
        ctx = _ctx(exposure_bias="mostly_cash")
        assert not apply_directional_policy("short", "SPY", ctx).allowed

    def test_mostly_cash_allows_sell(self):
        ctx = _ctx(exposure_bias="mostly_cash")
        assert apply_directional_policy("sell", "SPY", ctx).allowed

    def test_mostly_cash_allows_cover(self):
        ctx = _ctx(exposure_bias="mostly_cash")
        assert apply_directional_policy("cover", "SPY", ctx).allowed

    def test_net_long_blocks_short(self):
        ctx = _ctx(exposure_bias="net_long")
        assert not apply_directional_policy("short", "SPY", ctx).allowed

    def test_net_long_allows_buy(self):
        ctx = _ctx(exposure_bias="net_long")
        assert apply_directional_policy("buy", "SPY", ctx).allowed

    def test_net_short_blocks_buy(self):
        ctx = _ctx(exposure_bias="net_short")
        assert not apply_directional_policy("buy", "SPY", ctx).allowed

    def test_net_short_allows_short(self):
        ctx = _ctx(exposure_bias="net_short")
        assert apply_directional_policy("short", "SPY", ctx).allowed

    def test_neutral_allows_everything(self):
        ctx = _ctx(exposure_bias="neutral")
        for action in ("buy", "sell", "short", "cover"):
            assert apply_directional_policy(action, "SPY", ctx).allowed


class TestAdjustQuantity:
    def test_aggressive_increases(self):
        ctx = _ctx(agg_mult=1.4)
        assert adjust_quantity("buy", "SPY", 100, ctx) == 140

    def test_conservative_decreases(self):
        ctx = _ctx(agg_mult=0.6)
        assert adjust_quantity("buy", "SPY", 100, ctx) == 60

    def test_normal_no_change(self):
        ctx = _ctx(agg_mult=1.0)
        assert adjust_quantity("buy", "SPY", 100, ctx) == 100

    def test_raise_shrinks_buy(self):
        ctx = _ctx(cash_bias="raise")
        assert adjust_quantity("buy", "SPY", 100, ctx) == 70

    def test_deploy_grows_buy(self):
        ctx = _ctx(cash_bias="deploy")
        assert adjust_quantity("buy", "SPY", 100, ctx) == 120

    def test_raise_grows_sell(self):
        ctx = _ctx(cash_bias="raise")
        assert adjust_quantity("sell", "SPY", 50, ctx) == 60

    def test_hold_no_change_sell(self):
        ctx = _ctx(cash_bias="hold")
        assert adjust_quantity("sell", "SPY", 50, ctx) == 50

    def test_priority_boosts(self):
        ctx = _ctx(priority_set=frozenset({"SPY"}))
        assert adjust_quantity("buy", "SPY", 100, ctx) == 125

    def test_non_priority_no_boost(self):
        ctx = _ctx(priority_set=frozenset({"AAPL"}))
        assert adjust_quantity("buy", "SPY", 100, ctx) == 100

    def test_combined_deploy_and_priority(self):
        ctx = _ctx(cash_bias="deploy", priority_set=frozenset({"SPY"}))
        result = adjust_quantity("buy", "SPY", 100, ctx)
        assert result == int(int(100 * 1.2) * 1.25)  # 120 -> 150

    def test_zero_qty_returns_zero(self):
        ctx = _ctx()
        assert adjust_quantity("buy", "SPY", 0, ctx) == 0

    def test_min_qty_one_for_opens(self):
        ctx = _ctx(agg_mult=0.01)
        assert adjust_quantity("buy", "SPY", 1, ctx) == 1


class TestCashFloor:
    def test_no_trim_when_affordable(self):
        qty, reason = enforce_cash_floor(10, 100.0, 50_000.0, 5_000.0)
        assert qty == 10
        assert reason == ""

    def test_trim_when_breaching(self):
        qty, reason = enforce_cash_floor(100, 100.0, 12_000.0, 5_000.0)
        assert qty == 70
        assert "trimmed" in reason

    def test_zero_when_no_room(self):
        qty, reason = enforce_cash_floor(100, 100.0, 5_000.0, 5_000.0)
        assert qty == 0
        assert "breach" in reason

    def test_compute_cash_floor_basic(self):
        assert compute_cash_floor(100_000.0, 10.0) == pytest.approx(10_000.0)

    def test_compute_cash_floor_zero_equity(self):
        assert compute_cash_floor(0.0, 10.0) == 0.0


class TestBuildPolicyContext:
    def test_builds_from_decision(self):
        class FakeDecision:
            avoid_symbols = ["TSLA"]
            priority_symbols = ["AAPL"]
            aggression = "aggressive"
            cash_bias = "deploy"
            cash_target_pct = 15.0
            exposure_bias = "net_long"

        ctx = build_policy_context(FakeDecision())
        assert "TSLA" in ctx.avoid_set
        assert "AAPL" in ctx.priority_set
        assert ctx.agg_mult == 1.4
        assert ctx.cash_bias == "deploy"
        assert ctx.cash_target_pct == 15.0
        assert ctx.exposure_bias == "net_long"
