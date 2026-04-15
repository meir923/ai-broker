"""AI brain: two-tier decision system.

Tier 1 (code): fast_strategy.rank_symbols pre-screens candidates.
Tier 2 (Grok): qualitative analyst — sentiment, news, macro, final decisions.
"""

from __future__ import annotations

import logging
from typing import Any

from aibroker.agent.prompts import SYSTEM_PROMPT, MACRO_REGIME_PROMPT, format_user_prompt
from aibroker.agent.risk_profiles import RISK_PROFILES
from aibroker.agent.fast_strategy import (
    PrecomputedIndicators,
    rank_symbols,
    detect_bear_regime,
)
from aibroker.llm.grok import get_trading_client, get_macro_client

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
    __slots__ = ("actions", "market_view", "risk_note", "regime", "raw")

    def __init__(
        self,
        actions: list[AgentAction],
        market_view: str,
        risk_note: str,
        raw: dict[str, Any],
        regime: str = "",
    ):
        self.actions = actions
        self.market_view = market_view
        self.risk_note = risk_note
        self.regime = regime
        self.raw = raw

    def to_dict(self) -> dict[str, Any]:
        return {
            "actions": [a.to_dict() for a in self.actions],
            "market_view": self.market_view,
            "risk_note": self.risk_note,
            "regime": self.regime,
        }


def _get_grok():
    """Get the trading-specific Grok client."""
    return get_trading_client()


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


# ---------------------------------------------------------------------------
# Tier 1: Algorithmic candidate screening
# ---------------------------------------------------------------------------

def prepare_candidates(
    indicators: dict[str, PrecomputedIndicators],
    bar_index: int,
    symbols: list[str],
    risk_level: str,
    sentiment_scores: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Pre-screen candidates via fast_strategy, enrich with sentiment.

    Returns top N candidates sorted by |momentum| — includes both long and
    short opportunities for Grok to evaluate.
    """
    rp = RISK_PROFILES.get(risk_level, RISK_PROFILES["medium"])
    target = rp.get("target_positions", 7)
    top_n = max(target + 4, 10)
    weights = (
        rp.get("momentum_w10", 0.25),
        rp.get("momentum_w20", 0.40),
        rp.get("momentum_w50", 0.35),
    )

    ranked = rank_symbols(indicators, bar_index, symbols, weights)
    if not ranked:
        return []

    spy_ind = indicators.get("SPY")
    is_bear, spy_rsi = detect_bear_regime(spy_ind, bar_index, rp.get("bear_trigger", "below_200"))

    sent = sentiment_scores or {}

    candidates: list[dict[str, Any]] = []
    for r in ranked:
        sym = r["symbol"]
        mom = r["momentum"]
        rsi_val = r["rsi"]

        if mom > 0 and (rsi_val is None or rsi_val < 75):
            direction = "buy"
        elif mom < 0 or (rsi_val is not None and rsi_val > 70):
            direction = "short"
        else:
            direction = "buy"

        if is_bear and direction == "buy" and mom < 2:
            direction = "short"

        sym_sent = sent.get(sym, {})
        candidates.append({
            "symbol": sym,
            "price": r["price"],
            "momentum": r["momentum"],
            "rsi": r.get("rsi", 50),
            "atr": r.get("atr", 0),
            "ma20": r.get("ma20", 0),
            "ma50": r.get("ma50", 0),
            "above_ma50": r.get("above_ma50", True),
            "trend": "UP" if r.get("above_ma50") else "DOWN",
            "direction": direction,
            "sentiment": float(sym_sent.get("sentiment", 0)),
            "sentiment_summary": str(sym_sent.get("summary_he", sym_sent.get("summary", ""))),
        })

    candidates.sort(key=lambda c: abs(c["momentum"]), reverse=True)
    return candidates[:top_n]


# ---------------------------------------------------------------------------
# Tier 2: Grok as Chief Analyst
# ---------------------------------------------------------------------------

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
                regime=str(resp.get("regime", "")),
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
        regime=str(resp.get("regime", "")),
    )


# ---------------------------------------------------------------------------
# Macro morning call
# ---------------------------------------------------------------------------

_cached_regime: dict[str, str] = {}  # {"date": regime}


def assess_market_regime(
    news_headlines: list[dict[str, str]],
    current_date: str = "",
) -> str:
    """Daily macro regime assessment via Grok. Returns bullish/bearish/neutral.

    Cached per date so we only call Grok once per trading day.
    """
    if current_date and current_date in _cached_regime:
        return _cached_regime[current_date]

    if not news_headlines:
        return "neutral"

    grok = get_macro_client()
    titles = [h.get("title", "") for h in news_headlines[:20] if h.get("title")]
    if not titles:
        return "neutral"

    user_msg = "כותרות חדשות מהיום:\n" + "\n".join(f"- {t}" for t in titles)

    try:
        result = grok.chat_json(MACRO_REGIME_PROMPT, user_msg)
        regime = str(result.get("regime", "neutral")).lower()
        if regime not in ("bullish", "bearish", "neutral"):
            regime = "neutral"
        log.info("Macro regime assessment: %s (confidence: %s)", regime, result.get("confidence"))
    except Exception as e:
        log.warning("Macro regime call failed: %s", e)
        regime = "neutral"

    if current_date:
        _cached_regime[current_date] = regime
    return regime
