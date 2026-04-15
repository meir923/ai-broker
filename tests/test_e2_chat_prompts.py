"""E2+E4 — tests for llm/chat (build_context_snapshot) + agent/prompts"""
from __future__ import annotations

import pytest

from aibroker.llm.chat import build_context_snapshot
from aibroker.agent.prompts import (
    SYSTEM_PROMPT,
    MACRO_REGIME_PROMPT,
    RISK_INSTRUCTIONS,
    format_user_prompt,
)


class TestBuildContextSnapshot:
    def _cfg(self, context_modules: list[str]):
        """Minimal mock config."""
        from unittest.mock import MagicMock
        cfg = MagicMock()
        cfg.profile_name = "test"
        cfg.broker = "alpaca"
        cfg.account_mode = "paper"
        cfg.execution.dry_run = True
        cfg.grok.chat.context = context_modules
        cfg.risk.model_dump.return_value = {"kill_switch": False}
        return cfg

    def _state(self):
        from unittest.mock import MagicMock
        s = MagicMock()
        s.positions = [{"symbol": "SPY", "qty": 10}]
        s.open_orders = []
        s.recent_signals = []
        s.news_digest = "headlines"
        s.recent_errors = ["err1"]
        return s

    def test_profile_module(self):
        snap = build_context_snapshot(self._cfg(["profile"]), self._state())
        assert "profile" in snap
        assert snap["profile"]["name"] == "test"

    def test_risk_module(self):
        snap = build_context_snapshot(self._cfg(["risk"]), self._state())
        assert "risk" in snap

    def test_positions_module(self):
        snap = build_context_snapshot(self._cfg(["positions"]), self._state())
        assert "positions" in snap
        assert len(snap["positions"]) == 1

    def test_empty_context(self):
        snap = build_context_snapshot(self._cfg([]), self._state())
        assert snap == {}


class TestPrompts:
    def test_system_prompt_non_empty(self):
        assert len(SYSTEM_PROMPT) > 100

    def test_risk_instructions_all_levels(self):
        assert "low" in RISK_INSTRUCTIONS
        assert "medium" in RISK_INSTRUCTIONS
        assert "high" in RISK_INSTRUCTIONS

    def test_format_user_prompt_basic(self):
        snapshot = {
            "clock": {"ny_time": "10:00", "il_time": "17:00", "status": "OPEN"},
            "date": "2024-06-01",
            "portfolio": {
                "cash": 90000,
                "equity": 100000,
                "pnl": 0,
                "pnl_pct": 0,
                "positions": [],
            },
            "technicals": {
                "SPY": {"price": 500, "ma20": 495, "rsi14": 55, "atr14": 5.0, "trend": "UP"},
            },
            "candidates": [
                {"symbol": "SPY", "price": 500, "momentum": 5.2, "rsi": 55,
                 "trend": "UP", "direction": "buy", "sentiment": 0.3,
                 "sentiment_summary": "positive"},
            ],
            "news": [],
        }
        result = format_user_prompt(snapshot)
        assert "2024-06-01" in result
        assert "SPY" in result
        assert "Portfolio" in result

    def test_format_user_prompt_with_risk_level(self):
        snapshot = {
            "risk_level": "high",
            "clock": {"ny_time": "10:00", "il_time": "17:00", "status": "OPEN"},
            "date": "2024-06-01",
            "portfolio": {"cash": 90000, "equity": 100000, "pnl": 0, "pnl_pct": 0, "positions": []},
            "technicals": {},
            "news": [],
        }
        result = format_user_prompt(snapshot)
        assert "מוגברת" in result or "high" in result.lower() or "30%" in result

    def test_format_warns_few_positions(self):
        snapshot = {
            "clock": {"ny_time": "10:00", "il_time": "17:00", "status": "OPEN"},
            "date": "2024-06-01",
            "portfolio": {"cash": 90000, "equity": 100000, "pnl": 0, "pnl_pct": 0, "positions": [
                {"symbol": "SPY", "qty": 10, "avg_cost": 490, "current_price": 500, "unrealized_pnl": 100}
            ]},
            "technicals": {},
            "news": [],
        }
        result = format_user_prompt(snapshot)
        assert "WARNING" in result or "MUST" in result

    def test_format_with_news(self):
        snapshot = {
            "clock": {"ny_time": "10:00", "il_time": "17:00", "status": "OPEN"},
            "date": "2024-06-01",
            "portfolio": {"cash": 90000, "equity": 100000, "pnl": 0, "pnl_pct": 0, "positions": []},
            "technicals": {},
            "news": [{"symbol": "SPY", "title": "SPY surges 3%"}],
        }
        result = format_user_prompt(snapshot)
        assert "SPY surges" in result
        assert "Headlines" in result

    def test_format_with_regime(self):
        snapshot = {
            "clock": {"ny_time": "10:00", "il_time": "17:00", "status": "OPEN"},
            "date": "2024-06-01",
            "regime": "bearish",
            "portfolio": {"cash": 90000, "equity": 100000, "pnl": 0, "pnl_pct": 0, "positions": []},
            "technicals": {},
            "news": [],
        }
        result = format_user_prompt(snapshot)
        assert "BEARISH" in result

    def test_format_with_candidates(self):
        snapshot = {
            "clock": {"ny_time": "10:00", "il_time": "17:00", "status": "OPEN"},
            "date": "2024-06-01",
            "portfolio": {"cash": 90000, "equity": 100000, "pnl": 0, "pnl_pct": 0, "positions": []},
            "technicals": {},
            "news": [],
            "candidates": [
                {"symbol": "AAPL", "price": 180, "momentum": 3.5, "rsi": 60,
                 "trend": "UP", "direction": "buy", "sentiment": 0.5,
                 "sentiment_summary": "positive earnings"},
            ],
        }
        result = format_user_prompt(snapshot)
        assert "AAPL" in result
        assert "Candidates" in result
        assert "positive earnings" in result
