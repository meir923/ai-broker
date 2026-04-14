"""Alpaca paper execution for Plan B — always paper=True; kill-switch aware."""

from __future__ import annotations

from typing import Any, Literal

from aibroker.brokers.base import OrderIntent
from aibroker.planb.config import PlanBConfig
from aibroker.planb.risk_state import runtime_kill_switch_active


def planb_paper_status() -> dict[str, Any]:
    from aibroker.brokers.alpaca import AlpacaBrokerClient, alpaca_keys_set

    if not alpaca_keys_set():
        return {"ok": False, "error": "alpaca_keys_missing", "connected": False}
    client = AlpacaBrokerClient(paper=True)
    try:
        client.connect()
        acct = client.get_account()
        pos = client.positions()
        return {"ok": True, "connected": True, "account": acct, "positions": pos}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "connected": False}
    finally:
        client.disconnect()


def planb_paper_place(
    plan_cfg: PlanBConfig,
    *,
    symbol: str,
    side: Literal["buy", "sell"],
    quantity: float,
) -> dict[str, Any]:
    from aibroker.brokers.alpaca import AlpacaBrokerClient, alpaca_keys_set

    if runtime_kill_switch_active() or plan_cfg.risk.kill_switch:
        return {"ok": False, "error": "kill_switch_active"}
    sym = symbol.strip().upper()
    if sym not in plan_cfg.risk.allowed_symbols:
        return {"ok": False, "error": f"symbol_not_allowed:{sym}"}
    if not alpaca_keys_set():
        return {"ok": False, "error": "alpaca_keys_missing"}
    if quantity <= 0:
        return {"ok": False, "error": "bad_quantity"}

    client = AlpacaBrokerClient(paper=True)
    try:
        client.connect()
        intent = OrderIntent(
            symbol=sym,
            side=side,
            quantity=float(quantity),
            order_type="market",
            time_in_force="DAY",
        )
        res = client.place_order(intent)
        return {"ok": res.ok, "message": res.message, "broker_order_id": res.broker_order_id}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        client.disconnect()
