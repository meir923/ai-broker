"""Tests for aibroker/agent/mini_allocator.py — action ranking and trimming."""
from __future__ import annotations

import pytest
from aibroker.agent.intent_normalizer import NormalizedIntent, normalize
from aibroker.agent.meta_policy import PolicyContext
from aibroker.agent.mini_allocator import allocate


def _ctx(**overrides) -> PolicyContext:
    defaults = dict(
        avoid_set=frozenset(), priority_set=frozenset(),
        agg_mult=1.0, cash_bias="hold", cash_target_pct=10.0,
        exposure_bias="neutral",
    )
    defaults.update(overrides)
    return PolicyContext(**defaults)


def _intent(action, symbol, qty, cur_qty=0, reason="test"):
    return normalize(action, symbol, qty, cur_qty, reason)


class TestAllocatorOrdering:
    def test_exits_before_opens_when_defensive(self):
        ctx = _ctx(cash_bias="raise")
        intents = [
            _intent("buy", "AAPL", 50),       # open_long
            _intent("sell", "SPY", 50, 100),   # reduce_long (exit)
        ]
        result = allocate(intents, ctx, 100_000, 100_000, 10_000, price_fn=lambda s: 150.0)
        kinds = [i.kind for i in result.final_intents]
        assert kinds.index("reduce_long") < kinds.index("open_long")

    def test_priority_symbols_ranked_higher(self):
        ctx = _ctx(priority_set=frozenset({"AAPL"}))
        intents = [
            _intent("buy", "SPY", 50),
            _intent("buy", "AAPL", 50),
        ]
        result = allocate(intents, ctx, 100_000, 100_000, 10_000, price_fn=lambda s: 100.0)
        assert result.final_intents[0].symbol == "AAPL"


class TestAllocatorCashTrimming:
    def test_trims_buy_when_cash_tight(self):
        ctx = _ctx()
        intents = [_intent("buy", "SPY", 100)]
        result = allocate(intents, ctx, 12_000, 100_000, 5_000, price_fn=lambda s: 100.0)
        assert len(result.final_intents) == 1
        assert result.final_intents[0].final_qty == 70
        assert any("Trimmed" in n for n in result.notes)

    def test_drops_buy_when_no_room(self):
        ctx = _ctx()
        intents = [_intent("buy", "SPY", 100)]
        result = allocate(intents, ctx, 5_000, 100_000, 5_000, price_fn=lambda s: 100.0)
        assert len(result.final_intents) == 0
        assert len(result.dropped) == 1
        assert "cash" in result.dropped[0]["reason"]

    def test_exits_not_trimmed_by_cash(self):
        ctx = _ctx()
        intents = [_intent("sell", "SPY", 50, 100)]
        result = allocate(intents, ctx, 1_000, 100_000, 5_000, price_fn=lambda s: 100.0)
        assert len(result.final_intents) == 1
        assert result.final_intents[0].final_qty == 50


class TestAllocatorEmpty:
    def test_empty_input(self):
        result = allocate([], _ctx(), 100_000, 100_000, 10_000)
        assert result.final_intents == []
        assert result.dropped == []
