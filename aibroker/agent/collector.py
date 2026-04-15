"""Collect all data sources into a single snapshot for the agent brain."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from aibroker.data.historical import Bar

log = logging.getLogger(__name__)


def sma(bars: list[Bar], end: int, length: int) -> float | None:
    if end < length - 1:
        return None
    return sum(float(bars[i]["c"]) for i in range(end - length + 1, end + 1)) / length


def rsi(bars: list[Bar], end: int, period: int = 14) -> float | None:
    if end < period:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(end - period + 1, end + 1):
        delta = float(bars[i]["c"]) - float(bars[i - 1]["c"])
        if delta > 0:
            gains += delta
        else:
            losses -= delta
    if gains + losses < 1e-9:
        return 50.0
    rs = (gains / period) / max(losses / period, 1e-12)
    return round(100.0 - 100.0 / (1.0 + rs), 1)


def atr(bars: list[Bar], end: int, period: int = 14) -> float | None:
    if end < period:
        return None
    tr_sum = 0.0
    for i in range(end - period + 1, end + 1):
        h = float(bars[i]["h"])
        l = float(bars[i]["l"])
        pc = float(bars[i - 1]["c"]) if i > 0 else l
        tr_sum += max(h - l, abs(h - pc), abs(l - pc))
    return round(tr_sum / period, 4)


def trend_label(bars: list[Bar], end: int) -> str:
    ma10 = sma(bars, end, 10)
    ma50 = sma(bars, end, min(50, end + 1))
    if ma10 is None or ma50 is None:
        return "N/A"
    if ma10 > ma50 * 1.005:
        return "UP"
    if ma10 < ma50 * 0.995:
        return "DOWN"
    return "SIDEWAYS"


def roc(bars: list[Bar], idx: int, period: int) -> float | None:
    """Rate of Change (%) over `period` bars."""
    if idx < period:
        return None
    prev = float(bars[idx - period]["c"])
    if prev == 0:
        return None
    return round((float(bars[idx]["c"]) - prev) / prev * 100, 2)


def technicals_for_symbol(bars: list[Bar], idx: int) -> dict[str, Any]:
    close = float(bars[idx]["c"])
    ma50 = sma(bars, idx, min(50, idx + 1))
    a14 = atr(bars, idx, 14)
    result: dict[str, Any] = {
        "price": round(close, 2),
        "ma20": round(sma(bars, idx, 20) or 0, 2),
        "ma50": round(ma50 or 0, 2),
        "rsi14": rsi(bars, idx, 14),
        "atr14": a14,
        "atr_pct": round(a14 / close * 100, 2) if a14 and close > 0 else 0,
        "roc5": roc(bars, idx, 5),
        "roc20": roc(bars, idx, 20),
        "trend": trend_label(bars, idx),
        "volume": bars[idx].get("volume", 0),
    }
    if idx >= 5:
        recent = [float(bars[i]["c"]) for i in range(idx - 4, idx + 1)]
        result["last5"] = [round(p, 2) for p in recent]
    return result


def market_clock() -> dict[str, str]:
    ny_now = datetime.now(ZoneInfo("America/New_York"))
    il_now = datetime.now(ZoneInfo("Asia/Jerusalem"))
    ny_hour, ny_min = ny_now.hour, ny_now.minute
    il_hour, il_min = il_now.hour, il_now.minute

    ny_minutes = ny_hour * 60 + ny_min
    if 9 * 60 + 30 <= ny_minutes < 16 * 60:
        status = "OPEN"
    elif 4 * 60 <= ny_minutes < 9 * 60 + 30:
        status = "PRE_MARKET"
    elif 16 * 60 <= ny_minutes < 20 * 60:
        status = "AFTER_HOURS"
    else:
        status = "CLOSED"

    return {
        "ny_time": f"{ny_hour:02d}:{ny_min:02d}",
        "il_time": f"{il_hour:02d}:{il_min:02d}",
        "status": status,
    }


def collect_news(symbols: list[str]) -> list[dict[str, str]]:
    try:
        from aibroker.news.rss_fetcher import fetch_all_headlines, filter_headlines_for_symbol
        all_h = fetch_all_headlines()
        result: list[dict[str, str]] = []
        seen_titles: set[str] = set()
        for sym in symbols:
            for h in filter_headlines_for_symbol(all_h, sym, max_results=5):
                t = h.get("title", "")
                if t not in seen_titles:
                    seen_titles.add(t)
                    h["symbol"] = sym
                    result.append(h)
        for h in all_h[:10]:
            t = h.get("title", "")
            if t not in seen_titles:
                seen_titles.add(t)
                h["symbol"] = "MARKET"
                result.append(h)
        return result[:25]
    except Exception as e:
        log.warning("News collection failed: %s", e)
        return []


def enrich_with_sentiment(
    symbols: list[str],
    news_headlines: list[dict[str, str]],
    grok_client: Any = None,
) -> dict[str, dict[str, Any]]:
    """Score sentiment for each symbol using cached Grok analysis.

    Returns {symbol: {sentiment: float, summary_he: str, ...}}.
    Gracefully degrades to neutral on any failure.
    """
    try:
        from aibroker.news.sentiment import score_symbol_sentiment
        from aibroker.news.rss_fetcher import filter_headlines_for_symbol
    except ImportError:
        log.warning("Sentiment modules not available")
        return {}

    results: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        sym_headlines = filter_headlines_for_symbol(news_headlines, sym, max_results=8)
        results[sym] = score_symbol_sentiment(sym, sym_headlines, grok_client)
    return results


def build_snapshot(
    *,
    symbols: list[str],
    history: dict[str, list[Bar]],
    bar_index: int | None,
    positions: dict[str, dict[str, float]],
    cash: float,
    initial_deposit: float,
    news: list[dict[str, str]] | None = None,
    sim_date: str | None = None,
) -> dict[str, Any]:
    """Build a complete data snapshot for the agent brain."""

    technicals: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        bars = history.get(sym, [])
        idx = bar_index if bar_index is not None else len(bars) - 1
        if 0 <= idx < len(bars):
            technicals[sym] = technicals_for_symbol(bars, idx)

    equity = cash
    for sym, pos in positions.items():
        qty = pos.get("qty", 0)
        tech = technicals.get(sym, {})
        px = tech.get("price", pos.get("avg_cost", 0))
        equity += qty * px

    pnl = equity - initial_deposit
    pnl_pct = (pnl / initial_deposit * 100) if initial_deposit > 0 else 0

    pos_list = []
    for sym, pos in positions.items():
        qty = pos.get("qty", 0)
        avg = pos.get("avg_cost", 0)
        px = technicals.get(sym, {}).get("price", avg)
        upl = (px - avg) * qty if qty != 0 else 0
        pos_list.append({
            "symbol": sym,
            "qty": round(qty, 4),
            "avg_cost": round(avg, 2),
            "current_price": round(px, 2),
            "unrealized_pnl": round(upl, 2),
        })

    clock = market_clock()

    return {
        "clock": clock,
        "date": sim_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "portfolio": {
            "cash": round(cash, 2),
            "equity": round(equity, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "positions": pos_list,
        },
        "technicals": technicals,
        "news": news or [],
    }
