"""F3-F5 — tests for guardian limits, alerts helpers, persistence"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aibroker.agent.guardian import GUARDIAN_LIMITS, Guardian
from aibroker.agent.alerts import (
    _telegram_config,
    is_configured,
    send_alert,
)
from aibroker.agent.persistence import mark_stopped


class TestGuardianLimits:
    def test_all_levels_present(self):
        assert "low" in GUARDIAN_LIMITS
        assert "medium" in GUARDIAN_LIMITS
        assert "high" in GUARDIAN_LIMITS

    def test_low_stricter_than_high(self):
        assert GUARDIAN_LIMITS["low"]["daily_loss_pct"] < GUARDIAN_LIMITS["high"]["daily_loss_pct"]
        assert GUARDIAN_LIMITS["low"]["drawdown_pct"] < GUARDIAN_LIMITS["high"]["drawdown_pct"]

    def test_all_limits_positive(self):
        for level, lims in GUARDIAN_LIMITS.items():
            for key, val in lims.items():
                assert val > 0, f"{level}.{key} must be positive"


class TestGuardianInit:
    def test_create(self):
        g = Guardian(get_session=lambda: None, stop_session=lambda: None)
        assert g._check_interval == 30

    def test_start_stop(self):
        g = Guardian(get_session=lambda: None, stop_session=lambda: None)
        g.start()
        assert g._thread is not None
        g.stop()
        assert g._thread is None


class TestAlerts:
    def test_not_configured_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            assert is_configured() is False

    def test_configured_with_env(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}):
            assert is_configured() is True

    def test_send_alert_no_config(self):
        with patch.dict("os.environ", {}, clear=True):
            assert send_alert("test", "msg") is False

    def test_telegram_config_returns_tuple(self):
        token, chat_id = _telegram_config()
        assert isinstance(token, str)
        assert isinstance(chat_id, str)


class TestPersistence:
    def test_mark_stopped_no_crash(self):
        with patch("aibroker.data.storage.clear_agent_state"):
            mark_stopped()

    def test_mark_stopped_handles_error(self):
        with patch("aibroker.data.storage.clear_agent_state", side_effect=Exception("db error")):
            mark_stopped()  # should not raise
