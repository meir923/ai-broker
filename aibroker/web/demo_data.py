"""Deterministic demo series for dashboard charts until real backtest/live data exists."""

from __future__ import annotations

import hashlib
import math
import random
from typing import Any

from aibroker.config.schema import AppConfig


def _seed_from_profile(name: str) -> int:
    h = hashlib.sha256(name.encode()).digest()
    return int.from_bytes(h[:4], "big")


def build_demo_charts(cfg: AppConfig) -> dict[str, Any]:
    from aibroker.data.alpha_vantage import alpha_vantage_api_key, merge_into_demo_charts

    rnd = random.Random(_seed_from_profile(cfg.profile_name))
    n = 45
    labels = [f"יום {i + 1}" for i in range(n)]
    equity = 100_000.0
    values: list[float] = []
    for _ in range(n):
        equity *= 1 + rnd.uniform(-0.012, 0.014)
        values.append(round(equity, 2))

    symbols = cfg.risk.allowed_symbols or ["SPY", "QQQ", "DEMO"]
    weights = [rnd.random() for _ in symbols]
    s = sum(weights) or 1.0
    weights = [round(w / s, 3) for w in weights]

    ohlc = []
    px = 450.0 + rnd.uniform(-20, 20)
    for i in range(24):
        o = px
        px += rnd.uniform(-2.5, 2.5)
        h = max(o, px) + rnd.uniform(0, 1.2)
        l = min(o, px) - rnd.uniform(0, 1.2)
        c = px
        ohlc.append({"o": round(o, 2), "h": round(h, 2), "l": round(l, 2), "c": round(c, 2)})
        px = c

    used_daily_loss = min(0.35, rnd.random() * 0.5) * cfg.risk.max_daily_loss_usd
    used_notional = min(0.6, rnd.random() * 0.8) * cfg.risk.max_notional_per_trade_usd
    trades = min(cfg.risk.max_trades_per_day, int(rnd.random() * (cfg.risk.max_trades_per_day + 1)))

    base: dict[str, Any] = {
        "disclaimer": "נתוני הדגמה בלבד — יוחלפו בנתוני backtest / חי כשיתחברו.",
        "equity": {"labels": labels, "values": values},
        "weights": {"labels": symbols, "values": weights},
        "risk_usage": {
            "max_daily_loss_usd": cfg.risk.max_daily_loss_usd,
            "used_daily_loss_usd": round(used_daily_loss, 2),
            "max_notional_per_trade_usd": cfg.risk.max_notional_per_trade_usd,
            "used_notional_usd": round(used_notional, 2),
            "max_trades_per_day": cfg.risk.max_trades_per_day,
            "trades_today": trades,
        },
        "ohlc": ohlc,
        "sparkline": [round(50 + 40 * math.sin(i / 3) + rnd.uniform(-3, 3), 1) for i in range(32)],
    }
    key = alpha_vantage_api_key()
    if key:
        return merge_into_demo_charts(base, cfg.risk.allowed_symbols or [], key)
    base["alpha_vantage"] = {"enabled": False}
    return base
