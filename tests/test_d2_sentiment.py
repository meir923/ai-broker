"""D2-D3 — tests for news/sentiment + news/ingest"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aibroker.news.sentiment import (
    _cache_path,
    _read_cache,
    _write_cache,
    score_symbol_sentiment,
    score_all_symbols,
)


class TestSentimentCache:
    def test_cache_path_deterministic(self):
        a = _cache_path("SPY", date(2024, 6, 1))
        b = _cache_path("SPY", date(2024, 6, 1))
        assert a == b

    def test_cache_path_different_symbol(self):
        a = _cache_path("SPY", date(2024, 6, 1))
        b = _cache_path("AAPL", date(2024, 6, 1))
        assert a != b

    def test_read_cache_missing(self, tmp_path: Path):
        with patch("aibroker.news.sentiment.CACHE_DIR", tmp_path):
            assert _read_cache("SPY", date(2024, 6, 1)) is None

    def test_write_and_read_cache(self, tmp_path: Path):
        with patch("aibroker.news.sentiment.CACHE_DIR", tmp_path):
            data = {"sentiment": 0.5, "confidence": 0.8}
            _write_cache("SPY", date(2024, 6, 1), data)
            result = _read_cache("SPY", date(2024, 6, 1))
            assert result is not None
            assert result["sentiment"] == 0.5


class TestScoreSymbolSentiment:
    def test_empty_headlines_neutral(self):
        with patch("aibroker.news.sentiment._read_cache", return_value=None):
            r = score_symbol_sentiment("SPY", [])
        assert r["sentiment"] == 0.0
        assert r["headlines_used"] == 0

    def test_with_mock_grok(self):
        mock_grok = MagicMock()
        mock_grok.chat_json.return_value = {
            "sentiment": 0.7,
            "confidence": 0.9,
            "summary_he": "חיובי",
        }
        headlines = [{"title": "SPY hits all-time high"}]
        with patch("aibroker.news.sentiment._read_cache", return_value=None), \
             patch("aibroker.news.sentiment._write_cache"):
            r = score_symbol_sentiment("SPY", headlines, grok_client=mock_grok)
        assert r["sentiment"] == 0.7
        assert r["headlines_used"] == 1

    def test_cached_result_returned(self):
        cached = {"sentiment": 0.3, "confidence": 0.5, "headlines_used": 2}
        with patch("aibroker.news.sentiment._read_cache", return_value=cached):
            r = score_symbol_sentiment("SPY", [{"title": "news"}])
        assert r == cached


class TestScoreAllSymbols:
    def test_basic(self):
        mock_grok = MagicMock()
        mock_grok.chat_json.return_value = {"sentiment": 0.5, "confidence": 0.8}
        with patch("aibroker.news.sentiment._read_cache", return_value=None), \
             patch("aibroker.news.sentiment._write_cache"):
            result = score_all_symbols(
                ["SPY", "AAPL"],
                {"SPY": [{"title": "SPY up"}], "AAPL": []},
                grok_client=mock_grok,
            )
        assert "SPY" in result
        assert "AAPL" in result
        assert result["AAPL"] == 0.0  # no headlines

    def test_empty_symbols(self):
        result = score_all_symbols([], {})
        assert result == {}
