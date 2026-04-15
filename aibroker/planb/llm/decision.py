"""Optional LLM advisor for Plan B — JSON-shaped output, hard risk gate."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from aibroker.planb.config import PlanBLLMConfig, PlanBRiskConfig
from aibroker.planb.strategies.base import StrategySignal

log = logging.getLogger(__name__)


def apply_llm_risk_gate(
    *,
    action: str,
    symbol: str,
    equity_usd: float,
    risk: PlanBRiskConfig,
    llm: PlanBLLMConfig,
) -> tuple[StrategySignal, str]:
    """Map LLM action to signal after symbol / notional caps."""
    sym = symbol.strip().upper()
    if sym not in risk.allowed_symbols:
        return StrategySignal.NONE, "llm_symbol_not_allowed"
    if action not in ("buy", "sell", "hold"):
        return StrategySignal.NONE, "llm_bad_action"
    if action == "hold":
        return StrategySignal.NONE, "llm_hold"
    if action == "buy":
        return StrategySignal.BUY, "llm_buy"
    if action == "sell":
        return StrategySignal.SELL, "llm_sell"
    return StrategySignal.NONE, "llm_unknown"


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None


def maybe_llm_signal(
    *,
    user_payload: dict[str, Any],
    risk: PlanBRiskConfig,
    llm: PlanBLLMConfig,
    equity_usd: float,
) -> tuple[StrategySignal, str]:
    """
    If llm.enabled and Grok key present, ask model for a single decision.
    Always passes through apply_llm_risk_gate on parsed fields.
    """
    if not llm.enabled:
        return StrategySignal.NONE, "llm_disabled"

    try:
        from aibroker.llm.grok import get_trading_client
    except ImportError:
        return StrategySignal.NONE, "llm_no_client"

    try:
        client = get_trading_client()
    except Exception as exc:
        log.warning("Plan B LLM: Grok client init failed: %s", exc)
        return StrategySignal.NONE, "llm_no_key"

    system = (
        "You are a trading policy assistant for US equities simulation only. "
        "Reply with a single JSON object only, keys: action (buy|sell|hold), "
        "symbol (ticker), reason (short string). No prose outside JSON."
    )
    msg = json.dumps(user_payload, ensure_ascii=False)
    try:
        import httpx

        payload = {
            "model": client.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": msg},
            ],
            "temperature": 0.2,
        }
        with httpx.Client(timeout=client.timeout_s) as http:
            r = http.post(
                "https://api.x.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {client._key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
        reply = data["choices"][0]["message"]["content"]
    except Exception as exc:
        log.warning("Plan B LLM request failed: %s", exc)
        return StrategySignal.NONE, "llm_http_error"

    parsed = _extract_json_object(reply)
    if not parsed:
        return StrategySignal.NONE, "llm_bad_json"
    action = str(parsed.get("action", "hold")).lower()
    symbol = str(parsed.get("symbol", "")).upper()
    sig, why = apply_llm_risk_gate(
        action=action,
        symbol=symbol,
        equity_usd=equity_usd,
        risk=risk,
        llm=llm,
    )
    reason = str(parsed.get("reason", ""))[:200]
    if sig != StrategySignal.NONE:
        return sig, f"{why}:{reason}"
    return sig, why
