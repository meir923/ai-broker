"""E3 — tests for aibroker/agent/brain.py (parsing, _safe_int_quantity)"""
from __future__ import annotations

import pytest

from aibroker.agent.brain import (
    AgentAction,
    AgentDecision,
    _parse_actions,
    _safe_int_quantity,
)


# ── _safe_int_quantity ───────────────────────────────────────────────────

class TestSafeIntQuantity:
    def test_int(self):
        assert _safe_int_quantity(10) == 10

    def test_float(self):
        assert _safe_int_quantity(10.7) == 10

    def test_string_int(self):
        assert _safe_int_quantity("50") == 50

    def test_string_float(self):
        assert _safe_int_quantity("10.5") == 10

    def test_string_with_percent(self):
        assert _safe_int_quantity("50%") == 50

    def test_string_all(self):
        assert _safe_int_quantity("all") == 0

    def test_string_max(self):
        assert _safe_int_quantity("max") == 0

    def test_string_none_word(self):
        assert _safe_int_quantity("none") == 0

    def test_empty_string(self):
        assert _safe_int_quantity("") == 0

    def test_none(self):
        assert _safe_int_quantity(None) == 0

    def test_bool_false(self):
        assert _safe_int_quantity(False) == 0

    def test_bool_true(self):
        assert _safe_int_quantity(True) == 0

    def test_nan(self):
        assert _safe_int_quantity(float("nan")) == 0

    def test_garbage_string(self):
        assert _safe_int_quantity("buy_lots!") == 0

    def test_negative_int(self):
        assert _safe_int_quantity(-5) == -5

    def test_zero(self):
        assert _safe_int_quantity(0) == 0

    def test_very_large(self):
        assert _safe_int_quantity(999999999) == 999999999

    def test_string_with_spaces(self):
        assert _safe_int_quantity("  20  ") == 20


# ── _parse_actions ───────────────────────────────────────────────────────

class TestParseActions:
    def test_valid_buy(self):
        resp = {"actions": [{"symbol": "SPY", "action": "buy", "quantity": 10, "reason": "test"}]}
        actions, rejected = _parse_actions(resp, ["SPY"])
        assert len(actions) == 1
        assert actions[0].symbol == "SPY"
        assert actions[0].action == "buy"
        assert actions[0].quantity == 10

    def test_symbol_not_allowed(self):
        resp = {"actions": [{"symbol": "TSLA", "action": "buy", "quantity": 5, "reason": "test"}]}
        actions, rejected = _parse_actions(resp, ["SPY", "AAPL"])
        assert len(actions) == 0
        assert "TSLA" in rejected

    def test_no_allowed_list_means_any(self):
        resp = {"actions": [{"symbol": "ZZZZZ", "action": "buy", "quantity": 1, "reason": "test"}]}
        actions, rejected = _parse_actions(resp, None)
        assert len(actions) == 1

    def test_zero_qty_skipped(self):
        resp = {"actions": [{"symbol": "SPY", "action": "buy", "quantity": 0}]}
        actions, _ = _parse_actions(resp, None)
        assert len(actions) == 0

    def test_hold_action_passes_through(self):
        resp = {"actions": [{"symbol": "SPY", "action": "hold", "quantity": 0}]}
        actions, _ = _parse_actions(resp, ["SPY"])
        assert len(actions) == 1
        assert actions[0].action == "hold"

    def test_invalid_action_becomes_hold(self):
        resp = {"actions": [{"symbol": "SPY", "action": "yolo", "quantity": 10}]}
        actions, _ = _parse_actions(resp, ["SPY"])
        assert len(actions) == 1
        assert actions[0].action == "hold"

    def test_case_insensitive_symbol(self):
        resp = {"actions": [{"symbol": "spy", "action": "buy", "quantity": 10}]}
        actions, _ = _parse_actions(resp, ["SPY"])
        assert len(actions) == 1

    def test_empty_actions_list(self):
        actions, _ = _parse_actions({"actions": []}, ["SPY"])
        assert actions == []

    def test_missing_actions_key(self):
        actions, _ = _parse_actions({}, None)
        assert actions == []

    def test_short_and_cover(self):
        resp = {"actions": [
            {"symbol": "SPY", "action": "short", "quantity": 10},
            {"symbol": "AAPL", "action": "cover", "quantity": 5},
        ]}
        actions, _ = _parse_actions(resp, ["SPY", "AAPL"])
        assert len(actions) == 2
        assert actions[0].action == "short"
        assert actions[1].action == "cover"


# ── AgentAction / AgentDecision ──────────────────────────────────────────

class TestAgentModels:
    def test_action_to_dict(self):
        a = AgentAction("SPY", "buy", 10, "test")
        d = a.to_dict()
        assert d == {"symbol": "SPY", "action": "buy", "quantity": 10, "reason": "test"}

    def test_decision_to_dict(self):
        act = AgentAction("AAPL", "sell", 5, "tp")
        dec = AgentDecision([act], "bullish", "low risk", {"raw": True})
        d = dec.to_dict()
        assert len(d["actions"]) == 1
        assert d["market_view"] == "bullish"

    def test_empty_decision(self):
        dec = AgentDecision([], "", "", {})
        assert dec.to_dict()["actions"] == []
