"""Unit tests for AlpacaBrokerClient with mocked SDK."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from aibroker.brokers.alpaca import AlpacaBrokerClient, fetch_alpaca_quotes, _quote_book_cache
from aibroker.brokers.base import OrderIntent


@pytest.fixture(autouse=True)
def _clear_quote_cache():
    _quote_book_cache.clear()
    yield
    _quote_book_cache.clear()


@pytest.fixture()
def _env_keys(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")


def _mock_account():
    return SimpleNamespace(
        equity="100000.00",
        cash="95000.00",
        buying_power="190000.00",
        portfolio_value="100000.00",
        last_equity="99500.00",
        status="ACTIVE",
    )


def _mock_position():
    return SimpleNamespace(
        symbol="SPY",
        qty="10",
        avg_entry_price="450.00",
        market_value="4550.00",
        unrealized_pl="50.00",
        current_price="455.00",
    )


class TestAlpacaBrokerClient:
    def test_connect_success(self, _env_keys):
        mock_tc = MagicMock()
        mock_tc.get_account.return_value = _mock_account()
        mock_tc_cls = MagicMock(return_value=mock_tc)

        import sys
        fake_trading = MagicMock()
        fake_trading.TradingClient = mock_tc_cls
        sys.modules["alpaca"] = MagicMock()
        sys.modules["alpaca.trading"] = MagicMock()
        sys.modules["alpaca.trading.client"] = fake_trading
        try:
            client = AlpacaBrokerClient(paper=True)
            client.connect()
            acct = client.get_account()
            assert acct["equity_usd"] == 100_000.0
            assert acct["cash_usd"] == 95_000.0
            assert acct["paper"] is True
        finally:
            sys.modules.pop("alpaca.trading.client", None)
            sys.modules.pop("alpaca.trading", None)
            sys.modules.pop("alpaca", None)

    def test_connect_missing_keys(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        client = AlpacaBrokerClient(paper=True)
        with pytest.raises(RuntimeError, match="ALPACA_API_KEY"):
            client.connect()

    def test_positions_not_connected(self):
        client = AlpacaBrokerClient(paper=True)
        assert client.positions() == []

    def test_place_order_not_connected(self):
        client = AlpacaBrokerClient(paper=True)
        intent = OrderIntent(symbol="SPY", side="buy", quantity=1.0)
        result = client.place_order(intent)
        assert result.ok is False


class TestFetchAlpacaQuotes:
    def test_no_keys_returns_empty(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        assert fetch_alpaca_quotes(["SPY"]) == {}

    def test_quotes_with_mock(self, _env_keys, monkeypatch: pytest.MonkeyPatch):
        mock_quote = SimpleNamespace(ask_price=455.50, bid_price=455.40)
        mock_client = MagicMock()
        mock_client.get_stock_latest_quote.return_value = {"SPY": mock_quote}

        with patch("aibroker.brokers.alpaca.StockHistoricalDataClient", create=True) as mock_cls, \
             patch("aibroker.brokers.alpaca.StockLatestQuoteRequest", create=True):
            # Patch the imports inside fetch_alpaca_quotes
            import aibroker.brokers.alpaca as mod

            orig_fetch = mod.fetch_alpaca_quotes

            def patched_fetch(symbols):
                import importlib
                import sys
                mock_data_mod = MagicMock()
                mock_data_mod.StockHistoricalDataClient.return_value = mock_client
                mock_data_mod.StockLatestQuoteRequest = lambda **kw: kw
                sys.modules["alpaca.data.historical"] = mock_data_mod
                sys.modules["alpaca.data.requests"] = mock_data_mod
                try:
                    return orig_fetch(symbols)
                finally:
                    sys.modules.pop("alpaca.data.historical", None)
                    sys.modules.pop("alpaca.data.requests", None)

            result = patched_fetch(["SPY"])
            assert "SPY" in result
            assert abs(result["SPY"] - 455.45) < 0.01


class TestAlpacaApiEndpoints:
    """Verify the Alpaca API endpoints exist in the server (no real Alpaca connection needed)."""

    def test_alpaca_account_no_keys(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        from pathlib import Path
        from fastapi.testclient import TestClient
        from aibroker.web.server import create_app

        profile = Path(__file__).resolve().parents[1] / "config" / "profiles" / "paper_safe.yaml"
        app = create_app(profile, port=8765, open_browser=False)
        with TestClient(app) as client:
            r = client.get("/api/alpaca/account")
            assert r.status_code == 200
            data = r.json()
            assert isinstance(data.get("ok"), bool)

    def test_alpaca_quotes_no_keys(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        from pathlib import Path
        from fastapi.testclient import TestClient
        from aibroker.web.server import create_app

        profile = Path(__file__).resolve().parents[1] / "config" / "profiles" / "paper_safe.yaml"
        app = create_app(profile, port=8765, open_browser=False)
        with TestClient(app) as client:
            r = client.get("/api/alpaca/quotes")
            assert r.status_code == 200

    def test_status_includes_alpaca_flag(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        from pathlib import Path
        from fastapi.testclient import TestClient
        from aibroker.web.server import create_app

        profile = Path(__file__).resolve().parents[1] / "config" / "profiles" / "paper_safe.yaml"
        app = create_app(profile, port=8765, open_browser=False)
        with TestClient(app) as client:
            r = client.get("/api/status")
            assert r.status_code == 200
            data = r.json()
            assert "alpaca_keys_set" in data
