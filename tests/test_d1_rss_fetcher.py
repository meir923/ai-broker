"""D1 — tests for aibroker/news/rss_fetcher.py"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aibroker.news.rss_fetcher import (
    _cache_path,
    _is_cache_fresh,
    filter_headlines_for_symbol,
)


class TestCachePath:
    def test_deterministic(self):
        a = _cache_path("https://example.com/rss")
        b = _cache_path("https://example.com/rss")
        assert a == b

    def test_different_urls_different_paths(self):
        a = _cache_path("https://a.com")
        b = _cache_path("https://b.com")
        assert a != b

    def test_returns_json_suffix(self):
        assert _cache_path("https://x.com").suffix == ".json"


class TestIsCacheFresh:
    def test_nonexistent(self, tmp_path: Path):
        assert _is_cache_fresh(tmp_path / "nope.json") is False

    def test_fresh_file(self, tmp_path: Path):
        f = tmp_path / "ok.json"
        f.write_text("[]")
        assert _is_cache_fresh(f) is True


class TestFilterHeadlines:
    def _headlines(self) -> list[dict[str, str]]:
        return [
            {"title": "Apple stock rises 5%", "link": "http://a.com"},
            {"title": "NVIDIA beats earnings", "link": "http://b.com"},
            {"title": "S&P 500 hits new high", "link": "http://c.com"},
            {"title": "Tesla delivery numbers", "link": "http://d.com"},
            {"title": "Market roundup today", "link": "http://e.com"},
        ]

    def test_filter_by_ticker(self):
        result = filter_headlines_for_symbol(self._headlines(), "TSLA")
        assert len(result) == 1
        assert "Tesla" in result[0]["title"]

    def test_filter_by_company_name(self):
        result = filter_headlines_for_symbol(self._headlines(), "AAPL")
        assert len(result) == 1
        assert "Apple" in result[0]["title"]

    def test_filter_spy_matches_sp500(self):
        result = filter_headlines_for_symbol(self._headlines(), "SPY")
        assert len(result) == 1
        assert "S&P 500" in result[0]["title"]

    def test_no_match(self):
        result = filter_headlines_for_symbol(self._headlines(), "JPM")
        assert result == []

    def test_max_results(self):
        many = [{"title": f"Apple news {i}", "link": ""} for i in range(20)]
        result = filter_headlines_for_symbol(many, "AAPL", max_results=5)
        assert len(result) == 5

    def test_case_insensitive(self):
        h = [{"title": "apple AAPL stock update", "link": ""}]
        result = filter_headlines_for_symbol(h, "aapl")
        assert len(result) == 1

    def test_empty_headlines(self):
        assert filter_headlines_for_symbol([], "AAPL") == []

    def test_empty_title_skipped(self):
        h = [{"title": "", "link": ""}]
        assert filter_headlines_for_symbol(h, "AAPL") == []
