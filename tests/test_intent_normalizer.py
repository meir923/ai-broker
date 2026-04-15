"""Tests for aibroker/agent/intent_normalizer.py — semantic intent mapping."""
from __future__ import annotations

import pytest
from aibroker.agent.intent_normalizer import normalize, INTENT_KINDS


class TestBuyNormalization:
    def test_buy_on_flat_is_open_long(self):
        i = normalize("buy", "SPY", 50, current_qty=0)
        assert i.kind == "open_long"
        assert i.side_for_broker == "buy"
        assert i.opens_or_increases is True
        assert i.reduces_exposure is False

    def test_buy_on_existing_long_is_add_long(self):
        i = normalize("buy", "SPY", 50, current_qty=100)
        assert i.kind == "add_long"
        assert i.opens_or_increases is True

    def test_buy_on_short_is_reduce_short(self):
        i = normalize("buy", "SPY", 30, current_qty=-50)
        assert i.kind == "reduce_short"
        assert i.side_for_broker == "buy"
        assert i.opens_or_increases is False
        assert i.reduces_exposure is True

    def test_buy_on_short_full_close(self):
        i = normalize("buy", "SPY", 50, current_qty=-50)
        assert i.kind == "close_short"
        assert i.reduces_exposure is True


class TestSellNormalization:
    def test_sell_on_long_partial_is_reduce_long(self):
        i = normalize("sell", "SPY", 30, current_qty=100)
        assert i.kind == "reduce_long"
        assert i.side_for_broker == "sell"
        assert i.opens_or_increases is False
        assert i.reduces_exposure is True

    def test_sell_on_long_full_is_close_long(self):
        i = normalize("sell", "SPY", 100, current_qty=100)
        assert i.kind == "close_long"
        assert i.reduces_exposure is True

    def test_sell_on_flat_is_open_short(self):
        i = normalize("sell", "SPY", 50, current_qty=0)
        assert i.kind == "open_short"
        assert i.side_for_broker == "sell"
        assert i.opens_or_increases is True
        assert i.reduces_exposure is False

    def test_sell_on_existing_short_is_add_short(self):
        i = normalize("sell", "SPY", 50, current_qty=-100)
        assert i.kind == "add_short"
        assert i.opens_or_increases is True


class TestShortNormalization:
    def test_short_on_flat_is_open_short(self):
        i = normalize("short", "SPY", 50, current_qty=0)
        assert i.kind == "open_short"
        assert i.side_for_broker == "sell"
        assert i.opens_or_increases is True

    def test_short_on_existing_short_is_add_short(self):
        i = normalize("short", "SPY", 50, current_qty=-100)
        assert i.kind == "add_short"
        assert i.opens_or_increases is True

    def test_short_on_long_is_still_open_short(self):
        i = normalize("short", "SPY", 50, current_qty=100)
        assert i.kind == "open_short"
        assert i.opens_or_increases is True


class TestCoverNormalization:
    def test_cover_on_short_partial_is_reduce_short(self):
        i = normalize("cover", "SPY", 30, current_qty=-100)
        assert i.kind == "reduce_short"
        assert i.side_for_broker == "buy"
        assert i.opens_or_increases is False
        assert i.reduces_exposure is True

    def test_cover_on_short_full_is_close_short(self):
        i = normalize("cover", "SPY", 100, current_qty=-100)
        assert i.kind == "close_short"
        assert i.reduces_exposure is True

    def test_cover_on_flat_is_close_short_noop(self):
        i = normalize("cover", "SPY", 50, current_qty=0)
        assert i.kind == "close_short"
        assert i.reduces_exposure is False


class TestEdgeCases:
    def test_unknown_action_raises(self):
        with pytest.raises(ValueError, match="Unknown action"):
            normalize("hold", "SPY", 10, current_qty=0)

    def test_symbol_uppercased(self):
        i = normalize("buy", "spy", 10, current_qty=0)
        assert i.symbol == "SPY"

    def test_all_intent_kinds_covered(self):
        cases = [
            ("buy", 0, "open_long"),
            ("buy", 100, "add_long"),
            ("buy", -50, "close_short"),
            ("buy", -200, "reduce_short"),
            ("sell", 100, "close_long"),
            ("sell", 200, "reduce_long"),
            ("sell", 0, "open_short"),
            ("sell", -100, "add_short"),
            ("short", 0, "open_short"),
            ("short", -100, "add_short"),
            ("cover", -100, "close_short"),
            ("cover", -200, "reduce_short"),
        ]
        found_kinds = set()
        for action, cur, expected_kind in cases:
            qty = 100 if expected_kind != "reduce_long" else 50
            i = normalize(action, "SPY", qty, current_qty=cur)
            assert i.kind == expected_kind, f"action={action}, cur={cur} → {i.kind} != {expected_kind}"
            found_kinds.add(i.kind)
        assert found_kinds == set(INTENT_KINDS)
