from __future__ import annotations

import os
from pathlib import Path

import pytest

from aibroker.config.loader import load_profile, load_profile_dict
PROFILES = Path(__file__).resolve().parents[1] / "config" / "profiles"
PAPER_SAFE = PROFILES / "paper_safe.yaml"


def test_paper_safe_loads() -> None:
    cfg = load_profile(PAPER_SAFE)
    assert cfg.profile_name == "paper_safe"
    assert cfg.broker == "ibkr"
    assert cfg.account_mode == "paper"
    assert cfg.execution.dry_run is True
    assert cfg.grok.role == "news_only"


def test_live_requires_env() -> None:
    prev = os.environ.pop("I_ACCEPT_LIVE_RISK", None)
    try:
        with pytest.raises(ValueError, match="I_ACCEPT_LIVE_RISK"):
            load_profile_dict(
                {
                    "profile_name": "live_test",
                    "broker": "ibkr",
                    "account_mode": "live",
                    "execution": {"dry_run": True},
                    "grok": {
                        "enabled": False,
                        "role": "off",
                        "orders": {"approval": "manual"},
                        "chat": {"enabled": False, "ui": "cli", "context": []},
                    },
                    "strategy": {"mode": "rules"},
                    "signals": {"colmex": "off"},
                    "notifications": {"channel": "none"},
                    "risk": {
                        "max_daily_loss_usd": 100,
                        "max_notional_per_trade_usd": 50,
                        "max_trades_per_day": 1,
                        "kill_switch": False,
                        "allowed_symbols": ["SPY"],
                    },
                }
            )
    finally:
        if prev is not None:
            os.environ["I_ACCEPT_LIVE_RISK"] = prev


def test_auto_grok_orders_requires_env() -> None:
    prev = os.environ.pop("I_ACCEPT_LIVE_RISK", None)
    try:
        with pytest.raises(ValueError, match="I_ACCEPT_LIVE_RISK"):
            load_profile_dict(
                {
                    "profile_name": "auto",
                    "broker": "ibkr",
                    "account_mode": "paper",
                    "execution": {"dry_run": True},
                    "grok": {
                        "enabled": True,
                        "role": "order_proposals",
                        "orders": {"approval": "auto_within_risk"},
                        "chat": {"enabled": False, "ui": "cli", "context": []},
                    },
                    "strategy": {"mode": "rules"},
                    "signals": {"colmex": "off"},
                    "notifications": {"channel": "none"},
                    "risk": {
                        "max_daily_loss_usd": 100,
                        "max_notional_per_trade_usd": 50,
                        "max_trades_per_day": 1,
                        "kill_switch": False,
                        "allowed_symbols": ["SPY"],
                    },
                }
            )
    finally:
        if prev is not None:
            os.environ["I_ACCEPT_LIVE_RISK"] = prev
