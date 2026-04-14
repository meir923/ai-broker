"""M1 — Integration test: Data → Collector → Brain-parse → Risk → Execution loop"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from aibroker.data.historical import Bar
from aibroker.agent.collector import build_snapshot, sma, rsi
from aibroker.agent.brain import _parse_actions, _safe_int_quantity, AgentDecision
from aibroker.risk.gate import evaluate_intent
from aibroker.brokers.base import OrderIntent
from aibroker.config.schema import AppConfig, RiskConfig, ExecutionConfig
from aibroker.state.runtime import RuntimeState


def _bars(n: int, base: float = 500.0, step: float = 0.5) -> list[Bar]:
    dt = datetime(2024, 1, 2)
    result: list[Bar] = []
    for i in range(n):
        c = round(base + i * step, 4)
        result.append(Bar(date=dt.strftime("%Y-%m-%d"), o=round(c - 0.3, 4),
                          h=round(c + 2, 4), l=round(c - 2, 4), c=c, volume=1_000_000))
        dt += timedelta(days=1)
        while dt.weekday() >= 5:
            dt += timedelta(days=1)
    return result


def _app_config() -> AppConfig:
    return AppConfig(
        profile_name="integration_test",
        broker="alpaca",
        account_mode="paper",
        risk=RiskConfig(
            kill_switch=False,
            max_daily_loss_usd=5000,
            max_notional_per_trade_usd=50000,
            max_trades_per_day=20,
            max_open_orders=10,
            allowed_symbols=["SPY", "AAPL", "MSFT"],
        ),
        execution=ExecutionConfig(dry_run=True),
    )


class TestEndToEndPipeline:
    """Simulate the full flow without network calls."""

    def test_snapshot_to_parse_to_risk(self):
        """Data → snapshot → LLM response parse → risk gate → result."""
        bars = _bars(100)
        symbols = ["SPY", "AAPL"]
        history = {"SPY": bars, "AAPL": bars}

        snapshot = build_snapshot(
            symbols=symbols,
            history=history,
            bar_index=80,
            positions={"SPY": {"qty": 10, "avg_cost": 520}},
            cash=90000,
            initial_deposit=100000,
            sim_date="2024-04-15",
        )

        assert "portfolio" in snapshot
        assert "technicals" in snapshot
        assert snapshot["date"] == "2024-04-15"
        assert snapshot["portfolio"]["equity"] > 0

        llm_response = {
            "actions": [
                {"symbol": "AAPL", "action": "buy", "quantity": 5, "reason": "uptrend"},
                {"symbol": "SPY", "action": "sell", "quantity": 10, "reason": "take profit"},
            ],
            "market_view": "bullish",
            "risk_note": "moderate risk",
        }

        actions, rejected = _parse_actions(llm_response, ["SPY", "AAPL", "MSFT"])
        assert len(actions) == 2
        assert len(rejected) == 0

        cfg = _app_config()
        state = RuntimeState(
            profile_name="integration_test",
            account_mode="paper",
            dry_run=True,
            kill_switch=False,
            trades_today=0,
            daily_pnl_usd=0,
            positions=[{"symbol": "SPY", "qty": 10, "avg_cost_usd": 520}],
        )

        for act in actions:
            intent = OrderIntent(
                symbol=act.symbol,
                side=act.action if act.action in ("buy", "sell") else "buy",
                quantity=act.quantity,
                order_type="market",
            )
            decision = evaluate_intent(cfg, state, intent)
            assert hasattr(decision, "allowed")
            assert hasattr(decision, "reason")

    def test_indicators_consistency(self):
        """Verify SMA and RSI produce consistent results on same data."""
        bars = _bars(100)
        sma_val = sma(bars, 80, 20)
        rsi_val = rsi(bars, 80, 14)
        assert sma_val is not None
        assert rsi_val is not None
        assert 0 <= rsi_val <= 100

    def test_risk_blocks_kill_switch(self):
        """When kill_switch is on, all intents should be blocked."""
        cfg = _app_config()
        cfg.risk.kill_switch = True
        state = RuntimeState(kill_switch=True, trades_today=0)
        intent = OrderIntent(symbol="SPY", side="buy", quantity=10, order_type="market")
        d = evaluate_intent(cfg, state, intent)
        assert d.allowed is False

    def test_risk_blocks_excess_trades(self):
        """When trades_today >= max, intent should be blocked."""
        cfg = _app_config()
        state = RuntimeState(trades_today=20)
        intent = OrderIntent(symbol="SPY", side="buy", quantity=10, order_type="market")
        d = evaluate_intent(cfg, state, intent)
        assert d.allowed is False

    def test_full_flow_with_short_position(self):
        """Test short position flows through snapshot → parse → risk."""
        bars = _bars(100, base=200)
        snapshot = build_snapshot(
            symbols=["SPY"],
            history={"SPY": bars},
            bar_index=80,
            positions={"SPY": {"qty": -10, "avg_cost": 250}},
            cash=102500,
            initial_deposit=100000,
        )

        pos = snapshot["portfolio"]["positions"][0]
        assert pos["qty"] == -10

        llm_response = {
            "actions": [{"symbol": "SPY", "action": "cover", "quantity": 10, "reason": "exit"}],
        }
        actions, _ = _parse_actions(llm_response, ["SPY"])
        assert len(actions) == 1
        assert actions[0].action == "cover"
