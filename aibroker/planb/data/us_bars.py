"""US market bars for Plan B — daily via shared historical loader; intraday via Alpaca when available."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from aibroker.data.historical import Bar

log = logging.getLogger(__name__)


def intraday_rows_to_bars(rows: list[dict[str, Any]]) -> list[Bar]:
    """Convert Alpaca-style intraday dict rows to daily `Bar` shape (date + OHLCV)."""
    out: list[Bar] = []
    for r in rows:
        t = r.get("t")
        if t is None:
            continue
        if isinstance(t, str):
            ds = t
        elif hasattr(t, "isoformat"):
            ds = t.isoformat()
        else:
            ds = str(t)
        out.append(
            {
                "date": ds.replace("T", " ")[:26],
                "o": float(r.get("o", 0) or 0),
                "h": float(r.get("h", 0) or 0),
                "l": float(r.get("l", 0) or 0),
                "c": float(r.get("c", 0) or 0),
                "volume": int(r.get("v", 0) or 0),
            }
        )
    return out


def load_us_daily_bars(symbols: list[str], bars: int) -> dict[str, list[Bar]]:
    """Daily OHLCV for US symbols (Yahoo-backed cache in `historical`)."""
    from aibroker.data.historical import load_history

    syms = [str(s).upper().strip() for s in symbols if str(s).strip()]
    if not syms:
        syms = ["SPY"]
    return load_history(syms, bars=bars)


def load_us_intraday_placeholder(
    symbols: list[str], *, interval: str = "5m", bars: int = 100
) -> dict[str, list[dict[str, Any]]]:
    """Backward-compatible alias: same as `load_us_intraday` without Alpaca."""
    _ = interval
    return load_us_intraday(symbols, timeframe_minutes=5, limit=bars, prefer_alpaca=False)


def load_us_intraday(
    symbols: list[str],
    *,
    timeframe_minutes: int = 60,
    limit: int = 200,
    prefer_alpaca: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """
    Intraday OHLCV bars. When `prefer_alpaca` and Alpaca keys exist, uses Alpaca Data API v2.
    Otherwise returns empty lists per symbol (no Yahoo intraday in this build).
    """
    syms = [str(s).upper().strip() for s in symbols if str(s).strip()]
    if not syms:
        syms = ["SPY"]
    if not prefer_alpaca:
        return {s: [] for s in syms}
    try:
        return _load_us_intraday_alpaca(syms, timeframe_minutes=timeframe_minutes, limit=limit)
    except Exception as exc:
        log.info("Plan B intraday Alpaca unavailable: %s", exc)
        return {s: [] for s in syms}


def _load_us_intraday_alpaca(
    symbols: list[str], *, timeframe_minutes: int, limit: int
) -> dict[str, list[dict[str, Any]]]:
    from aibroker.brokers.alpaca import alpaca_keys_set

    if not alpaca_keys_set():
        return {s: [] for s in symbols}

    import os

    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    api_key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    tf = TimeFrame(timeframe_minutes, TimeFrameUnit.Minute)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=min(30, max(1, limit // 8)))
    lim = min(1000, max(10, limit))

    client = StockHistoricalDataClient(api_key, secret_key)
    out: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}

    for sym in symbols:
        req = StockBarsRequest(
            symbol_or_symbols=sym,
            timeframe=tf,
            start=start,
            end=end,
            limit=lim,
        )
        bs = client.get_stock_bars(req)
        rows: list[Any] = []
        if bs is None:
            continue
        try:
            rows = list(bs[sym])  # type: ignore[index]
        except Exception:
            try:
                rows = list(bs.data.get(sym, []))  # type: ignore[attr-defined]
            except Exception:
                rows = []
        for b in rows:
            ts = getattr(b, "timestamp", None)
            if ts is not None and hasattr(ts, "isoformat"):
                ts = ts.isoformat()
            out[sym].append(
                {
                    "t": ts,
                    "o": float(getattr(b, "open", 0) or 0),
                    "h": float(getattr(b, "high", 0) or 0),
                    "l": float(getattr(b, "low", 0) or 0),
                    "c": float(getattr(b, "close", 0) or 0),
                    "v": int(getattr(b, "volume", 0) or 0),
                }
            )
    return out
