"""
Alpha Vantage market data (optional). Requires ALPHA_VANTAGE_API_KEY in the environment or .env.

Free tier: 25 calls/day — we batch-load all symbols once on startup and cache aggressively.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import httpx

_LOG = logging.getLogger(__name__)

AV_BASE = "https://www.alphavantage.co/query"
_CACHE: dict[str, tuple[float, list[dict[str, float]] | None, str | None]] = {}
_CACHE_TTL_SEC = 3600.0

_BULK_OHLC: dict[str, list[dict[str, float]]] = {}
_BULK_LOADED = False
_BULK_LOCK = threading.Lock()
_BULK_LOAD_TIME: float = 0.0
_BULK_REFRESH_SEC = 3600.0


def alpha_vantage_api_key() -> str | None:
    k = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
    return k or None


def parse_time_series_daily(payload: dict[str, Any], *, max_candles: int) -> tuple[list[dict[str, float]] | None, str | None]:
    """Returns (ohlc list oldest->newest, error hint) or (None, reason)."""
    if "Note" in payload or "Information" in payload:
        msg = str(payload.get("Note") or payload.get("Information") or "rate limit or plan limit")
        return None, msg
    err = payload.get("Error Message") or payload.get("error message")
    if err:
        return None, str(err)
    ts = payload.get("Time Series (Daily)")
    if not isinstance(ts, dict) or not ts:
        return None, "no Time Series (Daily) in response"
    items = sorted(ts.items(), key=lambda x: x[0])
    slice_ = items[-max_candles:] if len(items) > max_candles else items
    out: list[dict[str, float]] = []
    for _date, row in slice_:
        try:
            out.append(
                {
                    "o": float(row["1. open"]),
                    "h": float(row["2. high"]),
                    "l": float(row["3. low"]),
                    "c": float(row["4. close"]),
                }
            )
        except (KeyError, TypeError, ValueError) as e:
            return None, f"bad candle row: {e}"
    return out, None


def fetch_daily_ohlc(
    symbol: str,
    api_key: str,
    *,
    max_candles: int = 24,
    timeout: float = 30.0,
) -> tuple[list[dict[str, float]] | None, str | None]:
    """
    Fetch compact daily series and return OHLC candles (chronological).
    On failure returns (None, reason).
    """
    sym = symbol.strip().upper()
    if not sym:
        return None, "empty symbol"
    cache_key = f"daily:{sym}:{max_candles}"
    now = time.monotonic()
    if cache_key in _CACHE:
        ts, data, err = _CACHE[cache_key]
        if now - ts < _CACHE_TTL_SEC and (data is not None or err):
            return data, err

    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": sym,
        "outputsize": "compact",
        "apikey": api_key,
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(AV_BASE, params=params)
            r.raise_for_status()
            payload = r.json()
    except (httpx.HTTPError, ValueError) as e:
        _LOG.warning("Alpha Vantage HTTP error: %s", e)
        _CACHE[cache_key] = (now, None, str(e))
        return None, str(e)

    ohlc, parse_err = parse_time_series_daily(payload, max_candles=max_candles)
    err = parse_err
    if ohlc is None and err:
        _LOG.warning("Alpha Vantage: %s", err)
    _CACHE[cache_key] = (now, ohlc, err)
    return ohlc, err


def bulk_load_ohlc(symbols: list[str], api_key: str, *, max_candles: int = 24) -> dict[str, list[dict[str, float]]]:
    """
    Load OHLC for multiple symbols with rate-limit protection.
    Alpha Vantage free tier = 25 calls/day, 5/min — we wait between calls.
    Returns {SYM: [ohlc...]} for successfully loaded symbols.
    """
    global _BULK_OHLC, _BULK_LOADED, _BULK_LOAD_TIME
    result: dict[str, list[dict[str, float]]] = {}
    for i, sym in enumerate(symbols):
        if i > 0 and i % 4 == 0:
            time.sleep(15)
        elif i > 0:
            time.sleep(1.5)
        ohlc, err = fetch_daily_ohlc(sym, api_key, max_candles=max_candles)
        if ohlc:
            result[sym.upper()] = ohlc
            _LOG.info("bulk_load: %s OK (%d candles)", sym, len(ohlc))
        else:
            _LOG.warning("bulk_load: %s failed: %s", sym, err)
            if err and ("rate" in err.lower() or "limit" in err.lower() or "premium" in err.lower()):
                _LOG.info("bulk_load: rate limit hit after %d symbols, stopping", i)
                break
    with _BULK_LOCK:
        _BULK_OHLC.update(result)
        _BULK_LOADED = True
        _BULK_LOAD_TIME = time.monotonic()
    return result


def get_cached_ohlc_all() -> dict[str, list[dict[str, float]]]:
    """Return whatever we have cached from bulk load. Never blocks."""
    with _BULK_LOCK:
        return dict(_BULK_OHLC)


def ensure_bulk_loaded(symbols: list[str]) -> None:
    """Trigger bulk load once (non-blocking after first call)."""
    global _BULK_LOADED
    api_key = alpha_vantage_api_key()
    if not api_key:
        return
    with _BULK_LOCK:
        if _BULK_LOADED and (time.monotonic() - _BULK_LOAD_TIME < _BULK_REFRESH_SEC):
            return
    t = threading.Thread(
        target=bulk_load_ohlc,
        args=(symbols, api_key),
        kwargs={"max_candles": 24},
        daemon=True,
        name="av-bulk-load",
    )
    t.start()


def merge_into_demo_charts(
    base: dict[str, Any],
    cfg_symbols: list[str],
    api_key: str,
) -> dict[str, Any]:
    """
    If fetch succeeds, replace ohlc + sparkline with Alpha Vantage daily data for the first symbol.
    """
    raw = cfg_symbols[0] if cfg_symbols else "SPY"
    sym = (raw.strip().upper() or "SPY")
    ohlc, err = fetch_daily_ohlc(sym, api_key, max_candles=24)
    out = dict(base)
    if not ohlc:
        out["alpha_vantage"] = {"enabled": True, "symbol": sym, "ok": False, "error": err or "unknown"}
        return out
    closes = [c["c"] for c in ohlc]
    out["ohlc"] = [{"o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"]} for c in ohlc]
    out["sparkline"] = [round(x, 2) for x in closes[-32:]]
    disc = str(out.get("disclaimer") or "")
    out["disclaimer"] = (
        disc
        + " מחירי OHLC לסימבול "
        + sym
        + " מ-Alpha Vantage (יומי). "
    ).strip()
    out["alpha_vantage"] = {
        "enabled": True,
        "symbol": sym,
        "ok": True,
        "source": "TIME_SERIES_DAILY",
    }
    return out
