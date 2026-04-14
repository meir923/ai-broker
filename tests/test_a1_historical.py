"""A1 — exhaustive tests for aibroker/data/historical.py"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aibroker.data.historical import (
    Bar,
    _cache_is_fresh,
    _cache_path,
    _gbm_fallback,
    _load_from_cache,
    _save_to_cache,
    load_history,
)


# ── helpers ──────────────────────────────────────────────────────────────

def _make_bars(n: int, start_price: float = 100.0, start_date: str = "2024-01-02") -> list[Bar]:
    bars: list[Bar] = []
    dt = datetime.strptime(start_date, "%Y-%m-%d")
    p = start_price
    for _ in range(n):
        bars.append(Bar(date=dt.strftime("%Y-%m-%d"), o=p, h=p + 1, l=p - 1, c=p + 0.5, volume=1_000_000))
        dt += timedelta(days=1)
        p += 0.5
    return bars


# ── Bar TypedDict ────────────────────────────────────────────────────────

class TestBarTypedDict:
    def test_bar_has_required_keys(self):
        b = Bar(date="2024-01-01", o=1.0, h=2.0, l=0.5, c=1.5, volume=100)
        assert set(b.keys()) == {"date", "o", "h", "l", "c", "volume"}

    def test_bar_accepts_zero_values(self):
        b = Bar(date="", o=0.0, h=0.0, l=0.0, c=0.0, volume=0)
        assert b["c"] == 0.0
        assert b["volume"] == 0


# ── _cache_path ──────────────────────────────────────────────────────────

class TestCachePath:
    def test_uppercase_symbol(self):
        p = _cache_path("aapl")
        assert p.name == "AAPL.json"

    def test_already_upper(self):
        assert _cache_path("SPY").name == "SPY.json"

    def test_mixed_case(self):
        assert _cache_path("GoOgL").name == "GOOGL.json"


# ── _cache_is_fresh ─────────────────────────────────────────────────────

class TestCacheIsFresh:
    def test_nonexistent_file(self, tmp_path: Path):
        assert _cache_is_fresh(tmp_path / "nope.json") is False

    def test_fresh_file(self, tmp_path: Path):
        f = tmp_path / "test.json"
        f.write_text("[]")
        assert _cache_is_fresh(f, max_age_hours=1) is True

    def test_stale_file(self, tmp_path: Path):
        f = tmp_path / "test.json"
        f.write_text("[]")
        import os
        old_time = time.time() - 25 * 3600
        os.utime(f, (old_time, old_time))
        assert _cache_is_fresh(f, max_age_hours=20) is False

    def test_zero_max_age(self, tmp_path: Path):
        f = tmp_path / "test.json"
        f.write_text("[]")
        assert _cache_is_fresh(f, max_age_hours=0) is False


# ── _load_from_cache / _save_to_cache ────────────────────────────────────

class TestCacheIO:
    def test_round_trip(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("aibroker.data.historical.CACHE_DIR", tmp_path)
        bars = _make_bars(50)
        _save_to_cache("AAPL", bars)
        loaded = _load_from_cache("AAPL")
        assert loaded is not None
        assert len(loaded) == 50
        assert loaded[0]["date"] == bars[0]["date"]

    def test_cache_returns_none_if_too_few_bars(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("aibroker.data.historical.CACHE_DIR", tmp_path)
        _save_to_cache("TINY", _make_bars(10))
        assert _load_from_cache("TINY") is None

    def test_cache_returns_none_for_corrupt_json(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("aibroker.data.historical.CACHE_DIR", tmp_path)
        p = tmp_path / "BAD.json"
        p.write_text("not json", encoding="utf-8")
        assert _load_from_cache("BAD") is None

    def test_cache_returns_none_if_not_list(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("aibroker.data.historical.CACHE_DIR", tmp_path)
        p = tmp_path / "OBJ.json"
        p.write_text('{"key": "val"}', encoding="utf-8")
        assert _load_from_cache("OBJ") is None


# ── _gbm_fallback ───────────────────────────────────────────────────────

class TestGBMFallback:
    def test_returns_correct_length(self):
        bars = _gbm_fallback("AAPL", 100)
        assert len(bars) == 100

    def test_all_bars_have_required_keys(self):
        for b in _gbm_fallback("SPY", 50):
            assert "date" in b and "o" in b and "h" in b and "l" in b and "c" in b

    def test_no_weekend_dates(self):
        bars = _gbm_fallback("QQQ", 200)
        for b in bars:
            dt = datetime.strptime(b["date"], "%Y-%m-%d")
            assert dt.weekday() < 5, f"Weekend date found: {b['date']}"

    def test_high_ge_low(self):
        for b in _gbm_fallback("TSLA", 300):
            assert b["h"] >= b["l"], f"h < l on {b['date']}"

    def test_volume_positive(self):
        for b in _gbm_fallback("NVDA", 100):
            assert b["volume"] >= 100_000

    def test_deterministic_seed(self):
        a = _gbm_fallback("AAPL", 50)
        b = _gbm_fallback("AAPL", 50)
        assert a[0]["o"] == b[0]["o"]

    def test_different_symbol_different_seed(self):
        a = _gbm_fallback("AAPL", 50)
        b = _gbm_fallback("MSFT", 50)
        assert a[0]["o"] != b[0]["o"]

    def test_prices_positive(self):
        for b in _gbm_fallback("AMD", 500):
            assert b["o"] > 0 and b["c"] > 0

    def test_unknown_symbol_uses_default_base(self):
        bars = _gbm_fallback("ZZZZZ", 10)
        assert len(bars) == 10
        assert bars[0]["o"] == pytest.approx(200.0, rel=0.01)

    def test_zero_bars(self):
        assert _gbm_fallback("SPY", 0) == []

    def test_one_bar(self):
        bars = _gbm_fallback("SPY", 1)
        assert len(bars) == 1


# ── load_history ─────────────────────────────────────────────────────────

class TestLoadHistory:
    def test_gbm_fallback_when_no_network(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("aibroker.data.historical.CACHE_DIR", tmp_path)
        with patch("aibroker.data.historical._download_yahoo_direct", return_value=None), \
             patch("aibroker.data.historical._download_yfinance", return_value=None):
            result = load_history(["SPY", "AAPL"], bars=100)
        assert "SPY" in result and "AAPL" in result
        assert len(result["SPY"]) == 100

    def test_cache_hit_skips_download(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("aibroker.data.historical.CACHE_DIR", tmp_path)
        bars = _make_bars(100)
        _save_to_cache("SPY", bars)
        mock_dl = MagicMock(return_value=None)
        with patch("aibroker.data.historical._download_yahoo_direct", mock_dl):
            result = load_history(["SPY"], bars=50)
        mock_dl.assert_not_called()
        assert len(result["SPY"]) == 50

    def test_bars_truncation(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("aibroker.data.historical.CACHE_DIR", tmp_path)
        _save_to_cache("MSFT", _make_bars(200))
        result = load_history(["MSFT"], bars=80)
        assert len(result["MSFT"]) == 80

    def test_symbol_uppercased(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("aibroker.data.historical.CACHE_DIR", tmp_path)
        with patch("aibroker.data.historical._download_yahoo_direct", return_value=None), \
             patch("aibroker.data.historical._download_yfinance", return_value=None):
            result = load_history(["aapl"], bars=50)
        assert "AAPL" in result

    def test_empty_symbols_list(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("aibroker.data.historical.CACHE_DIR", tmp_path)
        result = load_history([], bars=50)
        assert result == {}

    def test_yahoo_success_caches(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("aibroker.data.historical.CACHE_DIR", tmp_path)
        fake_bars = _make_bars(100)
        with patch("aibroker.data.historical._download_yahoo_direct", return_value=fake_bars):
            load_history(["GOOG"], bars=100)
        assert (tmp_path / "GOOG.json").exists()

    def test_yahoo_direct_fails_tries_yfinance(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("aibroker.data.historical.CACHE_DIR", tmp_path)
        fake_bars = _make_bars(80)
        yf_mock = MagicMock(return_value=fake_bars)
        with patch("aibroker.data.historical._download_yahoo_direct", return_value=None), \
             patch("aibroker.data.historical._download_yfinance", yf_mock):
            result = load_history(["NVDA"], bars=80)
        yf_mock.assert_called_once()
        assert len(result["NVDA"]) == 80
