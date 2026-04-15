"""E3 — tests for aibroker/agent/brain.py (parsing, candidates, regime)"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from aibroker.agent.brain import (
    AgentAction,
    AgentDecision,
    _parse_actions,
    _safe_int_quantity,
    prepare_candidates,
    assess_market_regime,
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
        assert "regime" in d

    def test_decision_regime_field(self):
        dec = AgentDecision([], "neutral", "", {}, regime="bearish")
        assert dec.regime == "bearish"
        assert dec.to_dict()["regime"] == "bearish"

    def test_empty_decision(self):
        dec = AgentDecision([], "", "", {})
        assert dec.to_dict()["actions"] == []
        assert dec.regime == ""


# ── prepare_candidates ───────────────────────────────────────────────────

def _make_indicators(symbols, n=100, base=100.0, step=1.0):
    """Build PrecomputedIndicators from synthetic bars."""
    from aibroker.data.historical import Bar
    from aibroker.agent.fast_strategy import PrecomputedIndicators
    from datetime import datetime, timedelta
    indicators = {}
    for sym in symbols:
        dt = datetime(2024, 1, 2)
        bars = []
        for i in range(n):
            c = round(base + i * step, 4)
            bars.append(Bar(
                date=dt.strftime("%Y-%m-%d"),
                o=round(c - 0.5, 4), h=round(c + 2, 4),
                l=round(c - 2, 4), c=c, volume=1_000_000,
            ))
            dt += timedelta(days=1)
            while dt.weekday() >= 5:
                dt += timedelta(days=1)
        indicators[sym] = PrecomputedIndicators(bars)
    return indicators


class TestPrepareCandidates:
    def test_returns_list(self):
        ind = _make_indicators(["SPY", "AAPL", "MSFT"], n=100)
        result = prepare_candidates(ind, 80, ["SPY", "AAPL", "MSFT"], "medium")
        assert isinstance(result, list)

    def test_candidate_fields(self):
        ind = _make_indicators(["SPY", "AAPL", "MSFT"], n=100)
        result = prepare_candidates(ind, 80, ["SPY", "AAPL", "MSFT"], "medium")
        if result:
            c = result[0]
            assert "symbol" in c
            assert "momentum" in c
            assert "direction" in c
            assert "sentiment" in c

    def test_sorted_by_momentum(self):
        ind = _make_indicators(["SPY", "AAPL", "MSFT"], n=100)
        result = prepare_candidates(ind, 80, ["SPY", "AAPL", "MSFT"], "medium")
        if len(result) >= 2:
            momenta = [abs(c["momentum"]) for c in result]
            assert momenta == sorted(momenta, reverse=True)

    def test_empty_if_not_enough_bars(self):
        ind = _make_indicators(["SPY"], n=30)
        result = prepare_candidates(ind, 20, ["SPY"], "medium")
        assert result == []

    def test_with_sentiment(self):
        ind = _make_indicators(["SPY"], n=100)
        sent = {"SPY": {"sentiment": 0.8, "summary_he": "חיובי מאוד"}}
        result = prepare_candidates(ind, 80, ["SPY"], "medium", sentiment_scores=sent)
        if result:
            assert result[0]["sentiment"] == 0.8
            assert result[0]["sentiment_summary"] == "חיובי מאוד"


# ── assess_market_regime ─────────────────────────────────────────────────

class TestAssessMarketRegime:
    def test_empty_news_returns_neutral(self):
        assert assess_market_regime([]) == "neutral"

    def test_no_titles_returns_neutral(self):
        assert assess_market_regime([{"symbol": "SPY"}]) == "neutral"

    @patch("aibroker.agent.brain.get_macro_client")
    def test_calls_grok_and_parses(self, mock_get):
        mock_grok = MagicMock()
        mock_grok.chat_json.return_value = {"regime": "bearish", "confidence": 0.8}
        mock_get.return_value = mock_grok
        from aibroker.agent.brain import _cached_regime
        _cached_regime.clear()
        result = assess_market_regime(
            [{"title": "Market crashes 10%"}],
            current_date="2099-01-01",
        )
        assert result == "bearish"

    @patch("aibroker.agent.brain.get_macro_client")
    def test_caches_per_date(self, mock_get):
        mock_grok = MagicMock()
        mock_grok.chat_json.return_value = {"regime": "bullish", "confidence": 0.9}
        mock_get.return_value = mock_grok
        from aibroker.agent.brain import _cached_regime
        _cached_regime.clear()
        r1 = assess_market_regime([{"title": "Bull run"}], current_date="2099-02-01")
        r2 = assess_market_regime([{"title": "Different news"}], current_date="2099-02-01")
        assert r1 == r2 == "bullish"
        assert mock_grok.chat_json.call_count == 1
