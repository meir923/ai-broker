"""I1 — tests for strategies/simple_rules.py (SwingPortfolioManager)"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

import pytest

from aibroker.strategies.simple_rules import (
    SwingPortfolioManager,
    _PositionMeta,
    RISK_PCT,
    MAX_POS_PCT_EQUITY,
    MAX_PORTFOLIO_EXPOSURE,
    MAX_CONCURRENT_POSITIONS,
)


def _bars(n: int, base: float = 100.0, step: float = 0.5) -> list[dict[str, Any]]:
    dt = datetime(2023, 6, 1)
    result: list[dict[str, Any]] = []
    for i in range(n):
        c = round(base + i * step, 4)
        result.append({"date": dt.strftime("%Y-%m-%d"),
                        "o": round(c - 0.3, 4), "h": round(c + 1.5, 4),
                        "l": round(c - 1.5, 4), "c": c, "volume": 1_000_000})
        dt += timedelta(days=1)
        while dt.weekday() >= 5:
            dt += timedelta(days=1)
    return result


class TestPositionMeta:
    def test_defaults(self):
        m = _PositionMeta()
        assert m.side == "flat"
        assert m.stop == 0.0
        assert m.pyramided is False
        assert m.symbol_leverage == 2.0


class TestSwingPortfolioManager:
    def test_evaluate_no_history(self):
        mgr = SwingPortfolioManager()
        intents = mgr.evaluate_all(
            bar_idx=0, history={}, positions={},
            equity=100_000, symbols=["SPY"]
        )
        assert intents == []

    def test_evaluate_short_history(self):
        mgr = SwingPortfolioManager()
        bars = _bars(30)
        intents = mgr.evaluate_all(
            bar_idx=20, history={"SPY": bars}, positions={},
            equity=100_000, symbols=["SPY"]
        )
        assert isinstance(intents, list)

    def test_evaluate_enough_data(self):
        mgr = SwingPortfolioManager()
        bars = _bars(200, base=100, step=0.5)
        intents = mgr.evaluate_all(
            bar_idx=150, history={"SPY": bars}, positions={},
            equity=100_000, symbols=["SPY"]
        )
        assert isinstance(intents, list)

    def test_get_meta_empty(self):
        mgr = SwingPortfolioManager()
        assert mgr.get_meta("AAPL") == {}

    def test_get_meta_after_evaluate(self):
        mgr = SwingPortfolioManager()
        bars = _bars(200, base=100, step=1)
        mgr.evaluate_all(
            bar_idx=150, history={"SPY": bars}, positions={},
            equity=100_000, symbols=["SPY"]
        )
        meta = mgr.get_meta("SPY")
        assert isinstance(meta, dict)

    def test_record_open_fill_price(self):
        mgr = SwingPortfolioManager()
        mgr._meta["SPY"] = _PositionMeta(entry_px=100)
        mgr.record_open_fill_price("SPY", 101.5)
        assert mgr._meta["SPY"].entry_px == 101.5

    def test_max_concurrent_positions_respected(self):
        mgr = SwingPortfolioManager()
        bars = _bars(200, base=100, step=0.5)
        symbols = [f"SYM{i}" for i in range(10)]
        history = {s: bars for s in symbols}
        positions = {s: {"qty": 10.0} for s in symbols[:MAX_CONCURRENT_POSITIONS]}
        intents = mgr.evaluate_all(
            bar_idx=150, history=history, positions=positions,
            equity=100_000, symbols=symbols
        )
        new_entries = [i for i, name in intents if "|" not in name]
        assert len(new_entries) <= MAX_CONCURRENT_POSITIONS

    def test_constants_reasonable(self):
        assert 0 < RISK_PCT < 0.1
        assert 0 < MAX_POS_PCT_EQUITY < 1.0
        assert MAX_PORTFOLIO_EXPOSURE > 0
        assert MAX_CONCURRENT_POSITIONS > 0
