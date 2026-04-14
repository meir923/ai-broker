from __future__ import annotations

import json
import logging
from typing import Any

from aibroker.config.schema import AppConfig
from aibroker.state.runtime import RuntimeState

log = logging.getLogger(__name__)


def build_context_snapshot(cfg: AppConfig, state: RuntimeState) -> dict[str, Any]:
    snap: dict[str, Any] = {}
    modules = set(cfg.grok.chat.context)
    if "profile" in modules:
        snap["profile"] = {
            "name": cfg.profile_name,
            "broker": cfg.broker,
            "account_mode": cfg.account_mode,
            "dry_run": cfg.execution.dry_run,
        }
    if "risk" in modules:
        snap["risk"] = cfg.risk.model_dump()
    if "positions" in modules:
        snap["positions"] = state.positions
    if "orders" in modules:
        snap["open_orders"] = state.open_orders
    if "signals" in modules:
        snap["recent_signals"] = state.recent_signals
    if "news_digest" in modules:
        snap["news_digest"] = state.news_digest
    if "logs_tail" in modules:
        snap["recent_errors"] = state.recent_errors[-20:]
    return snap


def chat_loop_placeholder(cfg: AppConfig, state: RuntimeState) -> None:
    from aibroker.llm.grok import GrokClient

    if not cfg.grok.chat.enabled:
        print("grok.chat.enabled is false — enable in profile to use chat.")
        return
    client = GrokClient()
    system = (
        "You are a trading copilot. Explain and analyze only. "
        "Do not claim you executed trades. Reply concisely."
    )
    ctx = build_context_snapshot(cfg, state)
    print("Context modules:", list(ctx.keys()))
    print("Type message (empty line to exit).")
    while True:
        line = input("> ").strip()
        if not line:
            break
        user = "Context JSON:\n" + json.dumps(ctx, indent=2) + "\n\nUser:\n" + line
        try:
            reply = client.chat_text(system, user)
            print(reply)
        except Exception as e:
            log.exception("chat failed")
            print(f"Error: {e}")
