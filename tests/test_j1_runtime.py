"""J1 — tests for aibroker/state/runtime.py"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aibroker.state.runtime import RuntimeState


class TestRuntimeState:
    def test_defaults(self):
        s = RuntimeState()
        assert s.profile_name == ""
        assert s.account_mode == ""
        assert s.dry_run is True
        assert s.kill_switch is False
        assert s.daily_pnl_usd == 0.0
        assert s.equity_usd == 0.0
        assert s.trades_today == 0
        assert s.positions == []
        assert s.open_orders == []

    def test_custom_values(self):
        s = RuntimeState(
            profile_name="paper_safe",
            daily_pnl_usd=-500.0,
            equity_usd=95000.0,
            trades_today=5,
            kill_switch=True,
            positions=[{"symbol": "SPY", "qty": 10}],
        )
        assert s.kill_switch is True
        assert s.daily_pnl_usd == -500.0
        assert len(s.positions) == 1

    def test_extra_fields_allowed(self):
        s = RuntimeState(custom_field="hello")
        assert s.custom_field == "hello"

    def test_updated_at_auto(self):
        s = RuntimeState()
        assert s.updated_at is not None
        assert s.updated_at.tzinfo is not None
