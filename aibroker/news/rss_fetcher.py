"""
RSS headline fetcher with disk caching.

Fetches from Yahoo Finance, MarketWatch, and Google Finance RSS feeds.
Caches headlines to disk with 1-hour TTL to avoid hammering feeds.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".aibroker" / "news_cache"
CACHE_TTL_SEC = 3600

TICKER_TO_COMPANY: dict[str, list[str]] = {
    "AAPL": ["Apple"],
    "MSFT": ["Microsoft"],
    "GOOGL": ["Google", "Alphabet"],
    "GOOG": ["Google", "Alphabet"],
    "AMZN": ["Amazon"],
    "TSLA": ["Tesla"],
    "META": ["Meta", "Facebook"],
    "NVDA": ["NVIDIA", "Nvidia"],
    "SPY": ["S&P 500", "S&P500", "SP500"],
    "QQQ": ["Nasdaq", "NASDAQ"],
    "AMD": ["AMD", "Advanced Micro"],
    "NFLX": ["Netflix"],
    "DIS": ["Disney"],
    "JPM": ["JPMorgan", "JP Morgan"],
    "V": ["Visa"],
    "BA": ["Boeing"],
    "INTC": ["Intel"],
    "WMT": ["Walmart"],
    "JNJ": ["Johnson & Johnson"],
    "PG": ["Procter & Gamble"],
}

RSS_FEEDS = [
    "https://finance.yahoo.com/news/rssindex",
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "https://news.google.com/rss/search?q=stock+market&hl=en-US&gl=US&ceid=US:en",
]


def _cache_path(feed_url: str) -> Path:
    h = hashlib.md5(feed_url.encode()).hexdigest()[:12]
    return CACHE_DIR / f"rss_{h}.json"


def _is_cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < CACHE_TTL_SEC


def _fetch_feed(url: str, timeout: float = 15.0) -> list[dict[str, str]]:
    """Fetch and parse a single RSS feed, returning list of {title, link, pubDate, source}."""
    cache = _cache_path(url)
    if _is_cache_fresh(cache):
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass

    items: list[dict[str, str]] = []
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(url, headers={"User-Agent": "AIBroker/1.0"})
            r.raise_for_status()
            root = ET.fromstring(r.text)

        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        for item in root.iter(f"{ns}item"):
            title_el = item.find(f"{ns}title")
            link_el = item.find(f"{ns}link")
            pub_el = item.find(f"{ns}pubDate")
            if title_el is None or not title_el.text:
                continue
            items.append({
                "title": (title_el.text or "").strip(),
                "link": (link_el.text or "").strip() if link_el is not None else "",
                "pubDate": (pub_el.text or "").strip() if pub_el is not None else "",
                "source": url,
            })
    except Exception as e:
        log.warning("RSS fetch failed for %s: %s", url, e)
        return items

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        cache.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.debug("Failed to write RSS cache: %s", e)

    return items


def fetch_all_headlines() -> list[dict[str, str]]:
    """Fetch headlines from all configured RSS feeds."""
    all_items: list[dict[str, str]] = []
    for url in RSS_FEEDS:
        all_items.extend(_fetch_feed(url))
    return all_items


def filter_headlines_for_symbol(
    headlines: list[dict[str, str]],
    symbol: str,
    max_results: int = 10,
) -> list[dict[str, str]]:
    """Filter headlines that mention the symbol ticker or known company name."""
    symbol_upper = symbol.upper()
    names = [symbol_upper] + [n.upper() for n in TICKER_TO_COMPANY.get(symbol_upper, [])]

    matched: list[dict[str, str]] = []
    for h in headlines:
        title_upper = h.get("title", "").upper()
        if any(name in title_upper for name in names):
            matched.append(h)
            if len(matched) >= max_results:
                break
    return matched


def fetch_symbol_headlines(symbol: str, max_headlines: int = 10) -> list[dict[str, str]]:
    """Convenience: fetch all feeds then filter for a single symbol."""
    all_h = fetch_all_headlines()
    return filter_headlines_for_symbol(all_h, symbol, max_headlines)
