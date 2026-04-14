"""B1 — exhaustive tests for aibroker/brokers/base.py"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from aibroker.brokers.base import OrderIntent, OrderResult


class TestOrderIntent:
    def test_valid_market_buy(self):
        oi = OrderIntent(symbol="AAPL", side="buy", quantity=10)
        assert oi.order_type == "market"
        assert oi.limit_price is None
        assert oi.time_in_force == "DAY"

    def test_valid_limit_sell(self):
        oi = OrderIntent(symbol="SPY", side="sell", quantity=5, order_type="limit", limit_price=500.0)
        assert oi.limit_price == 500.0

    def test_zero_quantity_rejected(self):
        with pytest.raises(ValidationError):
            OrderIntent(symbol="AAPL", side="buy", quantity=0)

    def test_negative_quantity_rejected(self):
        with pytest.raises(ValidationError):
            OrderIntent(symbol="AAPL", side="buy", quantity=-5)

    def test_fractional_quantity_allowed(self):
        oi = OrderIntent(symbol="AAPL", side="buy", quantity=0.5)
        assert oi.quantity == 0.5

    def test_invalid_side_rejected(self):
        with pytest.raises(ValidationError):
            OrderIntent(symbol="AAPL", side="short", quantity=10)

    def test_invalid_order_type_rejected(self):
        with pytest.raises(ValidationError):
            OrderIntent(symbol="AAPL", side="buy", quantity=10, order_type="stop")

    def test_empty_symbol_allowed_by_pydantic(self):
        oi = OrderIntent(symbol="", side="buy", quantity=1)
        assert oi.symbol == ""

    def test_client_tag_default_empty(self):
        oi = OrderIntent(symbol="X", side="buy", quantity=1)
        assert oi.client_tag == ""


class TestOrderResult:
    def test_ok_result(self):
        r = OrderResult(ok=True, message="filled", broker_order_id="abc-123")
        assert r.ok is True
        assert r.broker_order_id == "abc-123"

    def test_failed_result(self):
        r = OrderResult(ok=False, message="insufficient buying power")
        assert r.ok is False
        assert r.broker_order_id is None

    def test_raw_default_empty(self):
        r = OrderResult(ok=True, message="ok")
        assert r.raw == {}

    def test_raw_with_data(self):
        r = OrderResult(ok=True, message="ok", raw={"fill_price": 500.0})
        assert r.raw["fill_price"] == 500.0
