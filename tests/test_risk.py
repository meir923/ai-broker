from __future__ import annotations

from aibroker.brokers.base import OrderIntent
from aibroker.config.loader import load_profile_dict
from aibroker.risk.gate import evaluate_intent
from aibroker.state.runtime import RuntimeState


def _minimal_cfg(**overrides: object) -> dict:
    base = {
        "profile_name": "t",
        "broker": "ibkr",
        "account_mode": "paper",
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
            "max_daily_loss_usd": 1000,
            "max_notional_per_trade_usd": 500,
            "max_trades_per_day": 5,
            "kill_switch": False,
            "allowed_symbols": ["SPY", "QQQ"],
        },
    }
    base.update(overrides)
    return base


def test_risk_allows_spy() -> None:
    cfg = load_profile_dict(_minimal_cfg())
    st = RuntimeState(trades_today=0, daily_pnl_usd=0.0)
    intent = OrderIntent(symbol="SPY", side="buy", quantity=1.0)
    d = evaluate_intent(cfg, st, intent)
    assert d.allowed


def test_risk_blocks_symbol() -> None:
    cfg = load_profile_dict(_minimal_cfg())
    st = RuntimeState()
    intent = OrderIntent(symbol="AAPL", side="buy", quantity=1.0)
    d = evaluate_intent(cfg, st, intent)
    assert not d.allowed


def test_risk_blocks_empty_symbol() -> None:
    cfg = load_profile_dict(_minimal_cfg())
    st = RuntimeState()
    intent = OrderIntent(symbol="   ", side="buy", quantity=1.0)
    d = evaluate_intent(cfg, st, intent)
    assert not d.allowed
    assert "empty" in d.reason.lower()
