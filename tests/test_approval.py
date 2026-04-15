"""Tests for aibroker/agent/approval.py — unified approval logic."""
from __future__ import annotations

import pytest
from aibroker.agent.intent_normalizer import normalize
from aibroker.agent.approval import approve_sim, approve_live


class TestApproveSimDrawdown:
    def test_blocks_when_drawdown_exceeded(self):
        intent = normalize("buy", "SPY", 10, current_qty=0)
        r = approve_sim(
            intent, equity=55_000, initial_deposit=100_000,
            positions={}, est_price=150.0, risk_level="medium",
        )
        assert not r.allowed
        assert any("drawdown" in s for s in r.reasons)

    def test_allows_within_drawdown(self):
        intent = normalize("buy", "SPY", 10, current_qty=0)
        r = approve_sim(
            intent, equity=70_000, initial_deposit=100_000,
            positions={}, est_price=150.0, risk_level="medium",
        )
        assert r.allowed


class TestApproveSimExposure:
    def test_blocks_high_exposure(self):
        intent = normalize("buy", "SPY", 400, current_qty=0)
        r = approve_sim(
            intent, equity=100_000, initial_deposit=100_000,
            positions={}, est_price=150.0, risk_level="medium",
        )
        assert r.final_qty < 400 or not r.allowed

    def test_allows_small_exposure(self):
        intent = normalize("buy", "SPY", 10, current_qty=0)
        r = approve_sim(
            intent, equity=100_000, initial_deposit=100_000,
            positions={}, est_price=150.0, risk_level="medium",
        )
        assert r.allowed
        assert r.final_qty == 10

    def test_sell_to_close_not_blocked(self):
        intent = normalize("sell", "SPY", 100, current_qty=100)
        r = approve_sim(
            intent, equity=100_000, initial_deposit=100_000,
            positions={"SPY": {"qty": 100, "avg_cost": 150}},
            est_price=150.0, risk_level="medium",
        )
        assert r.allowed
        assert r.final_qty == 100


class TestApproveLive:
    def test_blocks_zero_price(self):
        intent = normalize("buy", "SPY", 10, current_qty=0)
        r = approve_live(
            intent, acct={"buying_power_usd": 50_000, "equity_usd": 100_000},
            positions={}, est_price=0, risk_level="medium",
            equity=100_000, margin_rate=0.34,
        )
        assert not r.allowed

    def test_caps_by_buying_power(self):
        intent = normalize("buy", "SPY", 1000, current_qty=0)
        intent.final_qty = 1000
        r = approve_live(
            intent, acct={"buying_power_usd": 10_000, "equity_usd": 100_000},
            positions={}, est_price=150.0, risk_level="medium",
            equity=100_000, margin_rate=0.34,
        )
        assert r.allowed
        assert r.final_qty < 1000

    def test_sell_to_close_passes(self):
        intent = normalize("sell", "SPY", 200, current_qty=200)
        r = approve_live(
            intent, acct={"buying_power_usd": 50_000, "equity_usd": 100_000},
            positions={"SPY": {"qty": 200, "avg_cost": 100}},
            est_price=150.0, risk_level="medium",
            equity=100_000, margin_rate=0.34,
        )
        assert r.allowed
        assert r.final_qty == 200

    def test_exposure_cap_on_open(self):
        intent = normalize("buy", "SPY", 500, current_qty=0)
        intent.final_qty = 500
        r = approve_live(
            intent, acct={"buying_power_usd": 200_000, "equity_usd": 100_000},
            positions={}, est_price=150.0, risk_level="medium",
            equity=100_000, margin_rate=0.34,
        )
        assert r.final_qty < 500 or not r.allowed

    def test_reports_reduction(self):
        intent = normalize("buy", "SPY", 500, current_qty=0)
        intent.final_qty = 500
        r = approve_live(
            intent, acct={"buying_power_usd": 200_000, "equity_usd": 100_000},
            positions={}, est_price=150.0, risk_level="medium",
            equity=100_000, margin_rate=0.34,
        )
        if r.allowed and r.reduced_from:
            assert r.reduced_from == 500
            assert r.final_qty < 500
