"""Risk profile definitions — single source of truth for simulation parameters."""

from __future__ import annotations

from typing import Any

RISK_PROFILES: dict[str, dict[str, Any]] = {
    "low": {
        "target_positions": 6,
        "rebalance_every": 15,
        "trail_pct": 0.88,
        "trail_atr_mult": 3.5,
        "invest_pct": 0.85,
        "allow_shorts": True,
        "bear_sell_all": False,
        "bear_trigger": "below_200",
        "rotation_threshold": -4.0,
        "label_he": "נמוך",
        "momentum_w10": 0.25,
        "momentum_w20": 0.40,
        "momentum_w50": 0.35,
        "rebalance_trim_above": 1.5,
    },
    "medium": {
        "target_positions": 7,
        "rebalance_every": 7,
        "trail_pct": 0.82,
        "trail_atr_mult": 4.5,
        "invest_pct": 0.95,
        "allow_shorts": True,
        "bear_sell_all": False,
        "bear_trigger": "below_200_and_50",
        "rotation_threshold": -2.0,
        "label_he": "בינוני",
        "momentum_w10": 0.25,
        "momentum_w20": 0.40,
        "momentum_w50": 0.35,
        "rebalance_trim_above": 1.4,
    },
    "high": {
        "target_positions": 8,
        "rebalance_every": 3,
        "trail_pct": 0.75,
        "trail_atr_mult": 5.5,
        "invest_pct": 1.0,
        "allow_shorts": True,
        "bear_sell_all": False,
        "bear_trigger": "below_200_and_50",
        "rotation_threshold": -1.0,
        "label_he": "מוגבר",
        "momentum_w10": 0.22,
        "momentum_w20": 0.38,
        "momentum_w50": 0.40,
        "rebalance_trim_above": 1.3,
    },
}
