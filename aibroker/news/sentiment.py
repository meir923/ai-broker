"""
Grok-powered per-symbol sentiment scoring.

Sends batched headlines to Grok API for sentiment analysis.
Results are cached per symbol per day to minimize API calls.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".aibroker" / "sentiment_cache"

SYSTEM_PROMPT = """\
You are a financial news sentiment analyst. You MUST answer in Hebrew.
For each set of headlines about a stock symbol:
1. Rate the aggregate sentiment from -1.0 (very bearish) to +1.0 (very bullish).
2. Explain your reasoning IN HEBREW.
3. Translate each headline title to Hebrew.
4. List the key factors that led to your conclusion IN HEBREW.

Return ONLY valid JSON with this exact structure:
{
  "sentiment": <float -1.0 to 1.0>,
  "confidence": <float 0.0 to 1.0>,
  "summary_he": "<one sentence summary in Hebrew>",
  "reasoning_he": "<2-3 sentences explaining WHY this score, in Hebrew>",
  "factors": ["<factor 1 in Hebrew>", "<factor 2 in Hebrew>"],
  "headlines_he": ["<headline 1 translated to Hebrew>", "<headline 2 translated to Hebrew>"]
}
If headlines are irrelevant to the symbol, return:
{"sentiment": 0.0, "confidence": 0.0, "summary_he": "לא רלוונטי", "reasoning_he": "הכותרות אינן קשורות לסימבול", "factors": [], "headlines_he": []}
"""


def _cache_path(symbol: str, day: date) -> Path:
    return CACHE_DIR / f"{symbol}_{day.isoformat()}.json"


def _read_cache(symbol: str, day: date) -> dict[str, Any] | None:
    p = _cache_path(symbol, day)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(symbol: str, day: date, data: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _cache_path(symbol, day).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        log.debug("Failed to write sentiment cache: %s", e)


def score_symbol_sentiment(
    symbol: str,
    headlines: list[dict[str, str]],
    grok_client: Any | None = None,
) -> dict[str, Any]:
    """
    Score sentiment for *symbol* given a list of headline dicts.

    Returns {"sentiment": float, "confidence": float, "summary": str, "headlines_used": int}.
    Falls back to neutral (0.0) on any failure.
    """
    today = date.today()
    cached = _read_cache(symbol, today)
    if cached is not None:
        return cached

    neutral = {"sentiment": 0.0, "confidence": 0.0, "summary": "no data", "headlines_used": 0}

    if not headlines:
        return neutral

    if grok_client is None:
        try:
            from aibroker.llm.grok import GrokClient
            grok_client = GrokClient()
        except Exception:
            log.warning("Cannot create GrokClient — returning neutral sentiment")
            return neutral

    titles = [h.get("title", "") for h in headlines[:10] if h.get("title")]
    if not titles:
        return neutral

    user_msg = (
        f"Symbol: {symbol}\n"
        f"Headlines ({len(titles)}):\n"
        + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(titles))
        + "\n\nProvide an aggregate sentiment score for this symbol."
    )

    try:
        result = grok_client.chat_json(SYSTEM_PROMPT, user_msg)
        result["headlines_used"] = len(titles)
        _write_cache(symbol, today, result)
        return result
    except Exception as e:
        log.warning("Grok sentiment call failed for %s: %s", symbol, e)
        return neutral


def score_all_symbols(
    symbols: list[str],
    headlines_by_symbol: dict[str, list[dict[str, str]]],
    grok_client: Any | None = None,
) -> dict[str, float]:
    """
    Return {symbol: sentiment_float} for each symbol.

    This is the main entry point called by the simulation engine.
    Symbols with no headlines get 0.0.
    """
    out: dict[str, float] = {}
    for sym in symbols:
        h = headlines_by_symbol.get(sym, [])
        result = score_symbol_sentiment(sym, h, grok_client)
        out[sym] = float(result.get("sentiment", 0.0))
    return out


def score_all_symbols_detailed(
    symbols: list[str],
    headlines_by_symbol: dict[str, list[dict[str, str]]],
    grok_client: Any | None = None,
) -> dict[str, dict[str, Any]]:
    """Like score_all_symbols but returns full Grok analysis per symbol."""
    out: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        h = headlines_by_symbol.get(sym, [])
        out[sym] = score_symbol_sentiment(sym, h, grok_client)
    return out
