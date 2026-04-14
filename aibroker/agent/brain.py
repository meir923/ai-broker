"""AI brain: sends snapshot to Grok and parses the trading decision."""

from __future__ import annotations

import logging
from typing import Any

from aibroker.agent.prompts import SYSTEM_PROMPT, format_user_prompt
from aibroker.llm.grok import GrokClient

log = logging.getLogger(__name__)


def _safe_int_quantity(raw: object) -> int:
    """Parse LLM quantity without crashing on strings like '50%%' or 'all'."""
    if raw is None:
        return 0
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if raw != raw:  # NaN
            return 0
        return int(raw)
    if isinstance(raw, str):
        s = raw.strip().lower()
        if not s or s in ("all", "max", "full", "none"):
            return 0
        s = s.rstrip("%").strip()
        try:
            n = float(s)
            return int(n)
        except ValueError:
            log.warning("Unparseable quantity from LLM: %r", raw)
            return 0
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        log.warning("Unparseable quantity from LLM: %r", raw)
        return 0


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


def _parse_actions(
    resp: dict[str, Any],
    allowed_symbols: list[str] | None,
) -> tuple[list[AgentAction], set[str]]:
    """Returns (actions, rejected_symbols_not_in_allowlist)."""
    allowed_u = [s.upper() for s in (allowed_symbols or [])]
    actions_raw = resp.get("actions", [])
    actions: list[AgentAction] = []
    rejected: set[str] = set()
    for a in actions_raw:
        sym = str(a.get("symbol", "")).upper()
        act = str(a.get("action", "hold")).lower()
        qty = _safe_int_quantity(a.get("quantity", 0))
        reason = str(a.get("reason", ""))
        if act not in ("buy", "sell", "hold", "short", "cover"):
            act = "hold"
        if allowed_symbols and sym not in allowed_u:
            if sym and act != "hold":
                rejected.add(sym)
            log.warning("Agent tried to trade %s but not in allowed list", sym)
            continue
        if qty <= 0 and act in ("buy", "sell", "short", "cover"):
            log.warning("Agent returned %s with qty %d, skipping", act, qty)
            continue
        actions.append(AgentAction(sym, act, qty, reason))
    return actions, rejected


def think(snapshot: dict[str, Any], allowed_symbols: list[str] | None = None) -> AgentDecision:
    grok = _get_grok()
    correction = ""
    resp: dict[str, Any] = {}
    for attempt in range(2):
        base = format_user_prompt(snapshot)
        user_msg = f"{correction}\n\n{base}" if correction else base
        log.info("Agent prompt (%d chars, attempt %d):\n%s", len(user_msg), attempt + 1, user_msg[:800])

        resp = grok.chat_json(SYSTEM_PROMPT, user_msg)
        log.info("Grok response: %s", resp)

        actions, rejected = _parse_actions(resp, allowed_symbols)
        if actions or not rejected or attempt == 1:
            return AgentDecision(
                actions=actions,
                market_view=str(resp.get("market_view", "")),
                risk_note=str(resp.get("risk_note", "")),
                raw=resp,
            )
        allow_txt = ", ".join(allowed_symbols or [])
        bad = ", ".join(sorted(rejected))
        correction = (
            f"[תיקון פנימי] ניסית לסחור בסימבולים שאינם ברשימה: {bad}. "
            f"המותרים בלבד: {allow_txt}. החזר JSON תקין רק עם סימבולים מותרים וכמויות חיוביות."
        )

    return AgentDecision(
        actions=[],
        market_view=str(resp.get("market_view", "")),
        risk_note=str(resp.get("risk_note", "")),
        raw=resp,
    )
