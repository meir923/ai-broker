"""Tests for aibroker/llm/grok.py — GrokClient upgrade: retry, JSON mode,
connection pooling, usage tracking, model tiers."""
from __future__ import annotations

import json
import os
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from aibroker.llm.grok import (
    GrokClient,
    UsageTracker,
    usage,
    get_trading_client,
    get_sentiment_client,
    get_macro_client,
    get_chat_client,
    DEFAULT_MODEL,
    COST_PER_1M,
    _clients,
)


# ── UsageTracker ─────────────────────────────────────────────────────────

class TestUsageTracker:
    def test_initial_state(self):
        t = UsageTracker()
        s = t.summary()
        assert s["calls"] == 0
        assert s["total_tokens"] == 0
        assert s["estimated_cost_usd"] == 0.0

    def test_record_adds_tokens(self):
        t = UsageTracker()
        t.record("grok-4.1-fast-non-reasoning", 1000, 500)
        s = t.summary()
        assert s["calls"] == 1
        assert s["prompt_tokens"] == 1000
        assert s["completion_tokens"] == 500
        assert s["total_tokens"] == 1500

    def test_cost_calculation(self):
        t = UsageTracker()
        t.record("grok-4.1-fast-non-reasoning", 1_000_000, 1_000_000)
        s = t.summary()
        assert s["estimated_cost_usd"] == pytest.approx(0.20 + 0.50, rel=0.01)

    def test_error_tracking(self):
        t = UsageTracker()
        t.record_error()
        t.record_error()
        assert t.summary()["errors"] == 2

    def test_reset(self):
        t = UsageTracker()
        t.record("grok-4.1-fast-non-reasoning", 1000, 500)
        t.record_error()
        t.reset()
        s = t.summary()
        assert s["calls"] == 0
        assert s["errors"] == 0

    def test_multiple_models(self):
        t = UsageTracker()
        t.record("grok-4.1-fast-non-reasoning", 1_000_000, 0)
        t.record("grok-4.20-non-reasoning", 1_000_000, 0)
        s = t.summary()
        assert s["calls"] == 2
        assert s["estimated_cost_usd"] == pytest.approx(0.20 + 2.00, rel=0.01)


# ── GrokClient configuration ────────────────────────────────────────────

class TestGrokClientConfig:
    def test_default_model(self):
        with patch.dict(os.environ, {"GROK_API_KEY": "test-key"}, clear=False):
            c = GrokClient()
            assert c.model == DEFAULT_MODEL

    def test_custom_model(self):
        with patch.dict(os.environ, {"GROK_API_KEY": "test-key"}, clear=False):
            c = GrokClient(model="grok-4.20-non-reasoning")
            assert c.model == "grok-4.20-non-reasoning"

    def test_env_override_model(self):
        with patch.dict(os.environ, {"GROK_API_KEY": "k", "GROK_MODEL": "grok-4"}, clear=False):
            c = GrokClient()
            assert c.model == "grok-4"

    def test_timeout_default(self):
        with patch.dict(os.environ, {"GROK_API_KEY": "k"}, clear=False):
            c = GrokClient()
            assert c.timeout_s == 90.0

    def test_timeout_env(self):
        with patch.dict(os.environ, {"GROK_API_KEY": "k", "GROK_TIMEOUT": "120"}, clear=False):
            c = GrokClient()
            assert c.timeout_s == 120.0

    def test_max_tokens_default(self):
        with patch.dict(os.environ, {"GROK_API_KEY": "k"}, clear=False):
            c = GrokClient()
            assert c.max_tokens == 2048

    def test_temperature_default(self):
        with patch.dict(os.environ, {"GROK_API_KEY": "k"}, clear=False):
            c = GrokClient()
            assert c.temperature == 0.2

    def test_missing_key_warns(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GROK_API_KEY", None)
            c = GrokClient()
            assert c._key == ""


# ── JSON mode and response parsing ──────────────────────────────────────

class TestChatJson:
    @patch.object(GrokClient, "_call_api")
    def test_parses_json_response(self, mock_api):
        mock_api.return_value = {
            "choices": [{"message": {"content": '{"actions": [], "market_view": "neutral"}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        with patch.dict(os.environ, {"GROK_API_KEY": "k"}, clear=False):
            c = GrokClient()
            result = c.chat_json("system", "user")
            assert result["market_view"] == "neutral"

    @patch.object(GrokClient, "_call_api")
    def test_strips_markdown_fences(self, mock_api):
        mock_api.return_value = {
            "choices": [{"message": {"content": '```json\n{"foo": "bar"}\n```'}}],
        }
        with patch.dict(os.environ, {"GROK_API_KEY": "k"}, clear=False):
            c = GrokClient()
            result = c.chat_json("system", "user")
            assert result["foo"] == "bar"

    @patch.object(GrokClient, "_call_api")
    def test_empty_response(self, mock_api):
        mock_api.return_value = {"choices": [{"message": {"content": ""}}]}
        with patch.dict(os.environ, {"GROK_API_KEY": "k"}, clear=False):
            c = GrokClient()
            result = c.chat_json("system", "user")
            assert "actions" in result  # fallback

    @patch.object(GrokClient, "_call_api")
    def test_no_choices(self, mock_api):
        mock_api.return_value = {"choices": []}
        with patch.dict(os.environ, {"GROK_API_KEY": "k"}, clear=False):
            c = GrokClient()
            result = c.chat_json("system", "user")
            assert "actions" in result

    @patch.object(GrokClient, "_call_api")
    def test_json_mode_in_payload(self, mock_api):
        mock_api.return_value = {
            "choices": [{"message": {"content": '{"a": 1}'}}],
        }
        with patch.dict(os.environ, {"GROK_API_KEY": "k"}, clear=False):
            c = GrokClient()
            c.chat_json("system", "user")
            payload = mock_api.call_args[0][0]
            assert payload["response_format"] == {"type": "json_object"}
            assert "max_tokens" in payload


# ── Factory functions ────────────────────────────────────────────────────

class TestFactoryFunctions:
    def setup_method(self):
        _clients.clear()

    @patch.dict(os.environ, {"GROK_API_KEY": "k"}, clear=False)
    def test_trading_client(self):
        c = get_trading_client()
        assert c.temperature == 0.15
        assert c.max_tokens == 2048

    @patch.dict(os.environ, {"GROK_API_KEY": "k"}, clear=False)
    def test_sentiment_client(self):
        c = get_sentiment_client()
        assert c.temperature == 0.2
        assert c.max_tokens == 1024

    @patch.dict(os.environ, {"GROK_API_KEY": "k"}, clear=False)
    def test_macro_client(self):
        c = get_macro_client()
        assert c.temperature == 0.2
        assert c.max_tokens == 512

    @patch.dict(os.environ, {"GROK_API_KEY": "k"}, clear=False)
    def test_chat_client(self):
        c = get_chat_client()
        assert c.temperature == 0.4
        assert c.max_tokens == 4096

    @patch.dict(os.environ, {"GROK_API_KEY": "k"}, clear=False)
    def test_singleton_reuse(self):
        c1 = get_trading_client()
        c2 = get_trading_client()
        assert c1 is c2

    @patch.dict(os.environ, {"GROK_API_KEY": "k", "GROK_TRADING_MODEL": "grok-4.20-non-reasoning"}, clear=False)
    def test_env_model_override(self):
        c = get_trading_client()
        assert c.model == "grok-4.20-non-reasoning"


# ── Cost table ───────────────────────────────────────────────────────────

class TestCostTable:
    def test_all_models_have_costs(self):
        for model in COST_PER_1M:
            inp, out = COST_PER_1M[model]
            assert inp > 0
            assert out > 0

    def test_default_model_in_table(self):
        assert DEFAULT_MODEL in COST_PER_1M
