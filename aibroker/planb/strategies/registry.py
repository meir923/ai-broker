from __future__ import annotations

from typing import Any

from aibroker.planb.strategies.base import Strategy
from aibroker.planb.strategies.ma_cross import MACrossStrategy
from aibroker.planb.strategies.momentum import MomentumStrategy


def build_strategy(strategy_id: str, params: dict[str, Any] | None = None) -> Strategy:
    p = params or {}
    sid = str(strategy_id).strip().lower()
    if sid == "ma_cross":
        return MACrossStrategy(
            fast=int(p.get("fast", 10)),
            slow=int(p.get("slow", 30)),
        )
    if sid == "momentum":
        return MomentumStrategy(
            lookback=int(p.get("lookback", 20)),
            entry_threshold_pct=float(p.get("entry_threshold_pct", 2.0)),
        )
    if sid == "llm_rules":
        # Rules-only fallback until LLM hook supplies overrides in sim/paper.
        return MACrossStrategy(fast=10, slow=30)
    raise ValueError(f"unknown strategy_id: {strategy_id!r}")
