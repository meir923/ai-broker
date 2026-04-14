"""
News ingestion pipeline — fetches RSS headlines and scores sentiment per symbol.
"""

from __future__ import annotations

import logging
from typing import Any

from aibroker.news.rss_fetcher import fetch_all_headlines, filter_headlines_for_symbol
from aibroker.news.sentiment import score_all_symbols

log = logging.getLogger(__name__)


def fetch_headlines_stub() -> list[dict[str, Any]]:
    """Legacy stub — kept for backward compat; returns real headlines now."""
    return fetch_all_headlines()


def fetch_sentiment_for_symbols(
    symbols: list[str],
    grok_client: Any | None = None,
) -> dict[str, float]:
    """
    Full pipeline: fetch RSS -> filter per symbol -> score via Grok.

    Returns {symbol: sentiment_float} where sentiment is in [-1.0, +1.0].
    Symbols without news get 0.0 (neutral).
    """
    all_headlines = fetch_all_headlines()
    log.info("Fetched %d total RSS headlines", len(all_headlines))

    by_symbol: dict[str, list[dict[str, str]]] = {}
    for sym in symbols:
        by_symbol[sym] = filter_headlines_for_symbol(all_headlines, sym, max_results=10)

    matched_count = sum(len(v) for v in by_symbol.values())
    log.info("Matched %d headlines across %d symbols", matched_count, len(symbols))

    return score_all_symbols(symbols, by_symbol, grok_client)
