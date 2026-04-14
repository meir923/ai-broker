"""
Disk-cached daily OHLC loader.
Primary source: Yahoo Finance (via requests).  Fallback: Geometric Brownian Motion.
"""

from __future__ import annotations

import json
import logging
import math
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict

log = logging.getLogger(__name__)

CACHE_DIR = Path("data/cache")
BARS_DEFAULT = 750


class Bar(TypedDict):
    date: str
    o: float
    h: float
    l: float
    c: float
    volume: int


def _cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol.upper()}.json"


def _cache_is_fresh(path: Path, max_age_hours: int = 20) -> bool:
    if not path.exists():
        return False
    age = datetime.now().timestamp() - path.stat().st_mtime
    return age < max_age_hours * 3600


def _load_from_cache(symbol: str) -> list[Bar] | None:
    p = _cache_path(symbol)
    if not _cache_is_fresh(p):
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list) and len(data) >= 30:
            return data
    except Exception:
        pass
    return None


def _save_to_cache(symbol: str, bars: list[Bar]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(symbol).write_text(
        json.dumps(bars, ensure_ascii=False), encoding="utf-8"
    )


def _download_yahoo_direct(symbol: str, bars: int) -> list[Bar] | None:
    """Download OHLC from Yahoo Finance chart API using requests (works with SSL proxies)."""
    try:
        import requests

        period_days = int(bars * 1.6) + 30
        end_ts = int(time.time())
        start_ts = end_ts - period_days * 86400

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {
            "period1": str(start_ts),
            "period2": str(end_ts),
            "interval": "1d",
            "includePrePost": "false",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 429:
            time.sleep(2)
            resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            log.warning("Yahoo chart API returned %d for %s", resp.status_code, symbol)
            return None

        data = resp.json()
        chart = data.get("chart", {}).get("result", [])
        if not chart:
            return None
        result_data = chart[0]
        timestamps = result_data.get("timestamp", [])
        quote = result_data.get("indicators", {}).get("quote", [{}])[0]
        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])
        volumes = quote.get("volume", [])

        if not timestamps or len(timestamps) < 30:
            return None

        result: list[Bar] = []
        for i, ts in enumerate(timestamps):
            o = opens[i] if i < len(opens) and opens[i] is not None else None
            h = highs[i] if i < len(highs) and highs[i] is not None else None
            l = lows[i] if i < len(lows) and lows[i] is not None else None
            c = closes[i] if i < len(closes) and closes[i] is not None else None
            v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0
            if o is None or h is None or l is None or c is None:
                continue
            dt = datetime.utcfromtimestamp(ts)
            result.append(Bar(
                date=dt.strftime("%Y-%m-%d"),
                o=round(float(o), 4),
                h=round(float(h), 4),
                l=round(float(l), 4),
                c=round(float(c), 4),
                volume=int(v),
            ))
        return result[-bars:] if len(result) >= 30 else None

    except Exception as exc:
        log.warning("Yahoo direct download failed for %s: %s", symbol, exc)
        return None


def _download_yfinance(symbol: str, bars: int) -> list[Bar] | None:
    try:
        import yfinance as yf
        period_days = int(bars * 1.6)
        tk = yf.Ticker(symbol)
        df = tk.history(period=f"{period_days}d", interval="1d")
        if df is None or len(df) < 30:
            return None
        result: list[Bar] = []
        for idx in df.index:
            row = df.loc[idx]
            result.append(Bar(
                date=str(idx.date()) if hasattr(idx, "date") else str(idx)[:10],
                o=round(float(row["Open"]), 4),
                h=round(float(row["High"]), 4),
                l=round(float(row["Low"]), 4),
                c=round(float(row["Close"]), 4),
                volume=int(row.get("Volume", 0)),
            ))
        return result[-bars:]
    except Exception as exc:
        log.warning("yfinance download failed for %s: %s", symbol, exc)
        return None


_REALISTIC_BASE: dict[str, float] = {
    "SPY": 527.0, "QQQ": 448.0, "AAPL": 198.0, "MSFT": 420.0, "NVDA": 880.0,
    "GOOGL": 175.0, "AMZN": 186.0, "META": 505.0, "TSLA": 175.0, "BRK.B": 415.0,
    "JPM": 205.0, "V": 280.0, "JNJ": 155.0, "UNH": 490.0, "WMT": 172.0,
    "PG": 165.0, "MA": 465.0, "HD": 360.0, "DIS": 112.0, "NFLX": 640.0,
    "PYPL": 68.0, "AMD": 160.0, "INTC": 31.0, "CRM": 270.0, "COST": 740.0,
    "PEP": 175.0, "KO": 62.0, "ABBV": 165.0, "MRK": 125.0, "BA": 178.0,
}


def _gbm_fallback(symbol: str, bars: int) -> list[Bar]:
    rng = random.Random(hash(symbol) & 0xFFFFFFFF)
    base = _REALISTIC_BASE.get(symbol.upper(), 200.0)
    mu = 0.0003
    sigma = 0.015
    price = base
    result: list[Bar] = []
    start = datetime.now() - timedelta(days=int(bars * 1.45))
    day = start
    for i in range(bars):
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        ret = mu + sigma * rng.gauss(0, 1)
        o = price
        price *= math.exp(ret)
        c = price
        intra_vol = abs(sigma * rng.gauss(0, 0.7))
        h = max(o, c) * (1 + intra_vol)
        l = min(o, c) * (1 - intra_vol)
        vol = int(rng.gauss(5_000_000, 2_000_000))
        result.append(Bar(
            date=day.strftime("%Y-%m-%d"),
            o=round(o, 4), h=round(h, 4), l=round(l, 4), c=round(c, 4),
            volume=max(vol, 100_000),
        ))
    return result


def load_history(
    symbols: list[str], bars: int = BARS_DEFAULT
) -> dict[str, list[Bar]]:
    result: dict[str, list[Bar]] = {}
    for sym in symbols:
        su = sym.upper()
        cached = _load_from_cache(su)
        if cached:
            result[su] = cached[-bars:]
            continue

        downloaded = _download_yahoo_direct(su, bars)
        if downloaded and len(downloaded) >= 30:
            _save_to_cache(su, downloaded)
            result[su] = downloaded[-bars:]
            log.info("loaded %d bars for %s from Yahoo direct", len(result[su]), su)
            continue

        downloaded = _download_yfinance(su, bars)
        if downloaded and len(downloaded) >= 30:
            _save_to_cache(su, downloaded)
            result[su] = downloaded[-bars:]
            log.info("loaded %d bars for %s from yfinance", len(result[su]), su)
            continue

        fb = _gbm_fallback(su, bars)
        result[su] = fb
        log.info("using GBM fallback for %s (%d bars)", su, len(fb))
    return result
