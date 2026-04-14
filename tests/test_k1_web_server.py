"""K1 — tests for web API models and basic route structure"""
from __future__ import annotations

import pytest

from aibroker.web.server import (
    PaperStartBody,
    GrokChatBody,
    PlanBQuickBacktestBody,
    PlanBBacktestRunBody,
    PlanBSimStartBody,
    AgentStartBody,
)


class TestPaperStartBody:
    def test_defaults(self):
        b = PaperStartBody()
        assert b.deposit_usd == 100_000.0
        assert b.interval_sec == 2.0
        assert b.leverage == 2.0
        assert b.start_date is None

    def test_valid(self):
        b = PaperStartBody(deposit_usd=50000, interval_sec=5, leverage=3.0, start_date="2024-01-01")
        assert b.deposit_usd == 50000

    def test_too_low_deposit(self):
        with pytest.raises(Exception):
            PaperStartBody(deposit_usd=10)

    def test_too_high_interval(self):
        with pytest.raises(Exception):
            PaperStartBody(interval_sec=5000)


class TestGrokChatBody:
    def test_valid(self):
        b = GrokChatBody(message="hello")
        assert b.message == "hello"

    def test_empty_rejected(self):
        with pytest.raises(Exception):
            GrokChatBody(message="")


class TestPlanBQuickBacktestBody:
    def test_defaults(self):
        b = PlanBQuickBacktestBody()
        assert b.symbols == ["SPY"]
        assert b.bars == 400

    def test_too_few_bars(self):
        with pytest.raises(Exception):
            PlanBQuickBacktestBody(bars=10)


class TestPlanBBacktestRunBody:
    def test_defaults(self):
        b = PlanBBacktestRunBody()
        assert b.symbol == "SPY"
        assert b.strategy_id == "ma_cross"


class TestPlanBSimStartBody:
    def test_defaults(self):
        b = PlanBSimStartBody()
        assert b.bar_source == "daily"
        assert b.timeframe_minutes == 60


class TestAgentStartBody:
    def test_defaults(self):
        b = AgentStartBody()
        assert b.mode == "sim"
        assert b.risk_level == "medium"
        assert len(b.symbols) == 10

    def test_invalid_mode(self):
        with pytest.raises(Exception):
            AgentStartBody(mode="turbo")

    def test_invalid_risk_level(self):
        with pytest.raises(Exception):
            AgentStartBody(risk_level="extreme")

    def test_custom_symbols(self):
        b = AgentStartBody(symbols=["TSLA", "NVDA"], deposit=50000)
        assert b.symbols == ["TSLA", "NVDA"]
        assert b.deposit == 50000
