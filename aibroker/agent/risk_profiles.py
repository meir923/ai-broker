"""Risk profile definitions — single source of truth for simulation parameters."""

from __future__ import annotations

from typing import Any

RISK_PROFILES: dict[str, dict[str, Any]] = {
    "low": {
        "target_positions": 6,
        "rebalance_every": 20,
        "trail_pct": 0.88,
        "trail_atr_mult": 4.0,
        "invest_pct": 0.80,
        "allow_shorts": False,
        "bear_sell_all": True,
        "bear_trigger": "below_200",
        "rotation_threshold": -5.0,
        "label_he": "נמוך",
        "momentum_w10": 0.25,
        "momentum_w20": 0.40,
        "momentum_w50": 0.35,
    },
    "medium": {
        "target_positions": 5,
        "rebalance_every": 10,
        "trail_pct": 0.82,
        "trail_atr_mult": 5.0,
        "invest_pct": 0.95,
        "allow_shorts": False,
        "bear_sell_all": True,
        "bear_trigger": "below_200_and_50",
        "rotation_threshold": -2.0,
        "label_he": "בינוני",
        "momentum_w10": 0.25,
        "momentum_w20": 0.40,
        "momentum_w50": 0.35,
    },
    "high": {
        "target_positions": 7,
        "rebalance_every": 5,
        "trail_pct": 0.75,
        "trail_atr_mult": 6.0,
        "invest_pct": 1.0,
        "allow_shorts": True,
        "bear_sell_all": True,
        "bear_trigger": "below_200_and_50",
        "rotation_threshold": -1.0,
        "label_he": "מוגבר",
        "momentum_w10": 0.22,
        "momentum_w20": 0.38,
        "momentum_w50": 0.40,
    },
}
