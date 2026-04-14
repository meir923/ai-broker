"""AI brain: sends snapshot to Grok and parses the trading decision."""

from __future__ import annotations

import logging
from typing import Any

from aibroker.agent.prompts import SYSTEM_PROMPT, format_user_prompt
from aibroker.llm.grok import GrokClient

log = logging.getLogger(__name__)


class AgentAction:
    __slots__ = ("symbol", "action", "quantity", "reason")

    def __init__(self, symbol: str, action: str, quantity: int, reason: str):
        self.symbol = symbol
        self.action = action
        self.quantity = quantity
        self.reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "quantity": self.quantity,
            "reason": self.reason,
        }


class AgentDecision:
    __slots__ = ("actions", "market_view", "risk_note", "raw")

    def __init__(
        self,
        actions: list[AgentAction],
        market_view: str,
        risk_note: str,
        raw: dict[str, Any],
    ):
        self.actions = actions
        self.market_view = market_view
        self.risk_note = risk_note
        self.raw = raw

    def to_dict(self) -> dict[str, Any]:
        return {
            "actions": [a.to_dict() for a in self.actions],
            "market_view": self.market_view,
            "risk_note": self.risk_note,
        }


_grok: GrokClient | None = None


def _get_grok() -> GrokClient:
    global _grok
    if _grok is None:
        _grok = GrokClient(model="grok-3-mini-fast")
    return _grok


def think(snapshot: dict[str, Any], allowed_symbols: list[str] | None = None) -> AgentDecision:
    user_msg = format_user_prompt(snapshot)
    log.info("Agent prompt (%d chars):\n%s", len(user_msg), user_msg[:800])

    grok = _get_grok()
    resp = grok.chat_json(SYSTEM_PROMPT, user_msg)
    log.info("Grok response: %s", resp)

    actions_raw = resp.get("actions", [])
    actions: list[AgentAction] = []
    for a in actions_raw:
        sym = str(a.get("symbol", "")).upper()
        act = str(a.get("action", "hold")).lower()
        qty = int(a.get("quantity", 0))
        reason = str(a.get("reason", ""))
        if act not in ("buy", "sell", "hold", "short", "cover"):
            act = "hold"
        if allowed_symbols and sym not in [s.upper() for s in allowed_symbols]:
            log.warning("Agent tried to trade %s but not in allowed list", sym)
            continue
        if qty <= 0 and act in ("buy", "sell", "short", "cover"):
            log.warning("Agent returned %s with qty %d, skipping", act, qty)
            continue
        actions.append(AgentAction(sym, act, qty, reason))

    return AgentDecision(
        actions=actions,
        market_view=str(resp.get("market_view", "")),
        risk_note=str(resp.get("risk_note", "")),
        raw=resp,
    )
