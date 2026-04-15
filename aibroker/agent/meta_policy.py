"""Shared meta-policy logic for sim and live agent paths.

Centralizes handling of: avoid_symbols, priority_symbols, aggression,
cash_bias, cash_target_pct, exposure_bias.  Both _tick_sim() and
_tick_live() delegate to these helpers instead of duplicating if-blocks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

AGG_MULTIPLIERS = {"conservative": 0.6, "normal": 1.0, "aggressive": 1.4}


@dataclass(frozen=True)
class PolicyContext:
    """Immutable snapshot of meta-policy extracted from a decision."""
    avoid_set: frozenset[str]
    priority_set: frozenset[str]
    agg_mult: float
    cash_bias: str          # deploy | hold | raise
    cash_target_pct: float
    exposure_bias: str      # net_long | neutral | net_short | mostly_cash


def build_policy_context(decision: Any) -> PolicyContext:
    return PolicyContext(
        avoid_set=frozenset(s.upper() for s in decision.avoid_symbols),
        priority_set=frozenset(s.upper() for s in decision.priority_symbols),
        agg_mult=AGG_MULTIPLIERS.get(decision.aggression, 1.0),
        cash_bias=decision.cash_bias,
        cash_target_pct=decision.cash_target_pct,
        exposure_bias=decision.exposure_bias,
    )


@dataclass
class DirectionalResult:
    allowed: bool
    reason: str = ""


def apply_directional_policy(
    action: str,
    symbol: str,
    ctx: PolicyContext,
) -> DirectionalResult:
    """Check whether *action* on *symbol* is allowed by the meta-policy.

    Returns DirectionalResult with allowed=False and a reason if blocked.
    """
    sym = symbol.upper()
    if sym in ctx.avoid_set:
        return DirectionalResult(False, f"{action} {symbol} blocked — in avoid_symbols")

    eb = ctx.exposure_bias
    if action in ("buy", "short") and eb == "mostly_cash":
        return DirectionalResult(False, f"{action} {symbol} blocked — exposure_bias is mostly_cash")
    if action == "short" and eb == "net_long":
        return DirectionalResult(False, f"short {symbol} blocked — exposure_bias is net_long")
    if action == "buy" and eb == "net_short":
        return DirectionalResult(False, f"buy {symbol} blocked — exposure_bias is net_short")

    return DirectionalResult(True)


def adjust_quantity(
    action: str,
    symbol: str,
    base_qty: int,
    ctx: PolicyContext,
) -> int:
    """Apply aggression, cash_bias, and priority multipliers to *base_qty*."""
    if base_qty <= 0:
        return 0

    sym = symbol.upper()
    qty = float(base_qty)

    if action in ("buy", "short"):
        if ctx.agg_mult != 1.0:
            qty *= ctx.agg_mult
        if ctx.cash_bias == "raise":
            qty *= 0.7
        elif ctx.cash_bias == "deploy":
            qty *= 1.2
        if sym in ctx.priority_set:
            qty *= 1.25
        return max(1, int(qty))

    if action in ("sell", "cover") and ctx.cash_bias == "raise":
        return max(base_qty, int(base_qty * 1.2))

    return base_qty


def enforce_cash_floor(
    qty: int,
    est_price: float,
    available_cash: float,
    cash_floor: float,
) -> tuple[int, str]:
    """Cap *qty* so that a buy does not breach the cash floor.

    Returns (capped_qty, reason).  reason is empty when no trim needed.
    """
    if est_price <= 0 or qty <= 0:
        return qty, ""
    if available_cash - (qty * est_price) >= cash_floor:
        return qty, ""
    affordable = max(0, int((available_cash - cash_floor) / max(est_price, 1)))
    if affordable <= 0:
        return 0, "would breach cash_target_pct"
    return affordable, f"trimmed from {qty} to {affordable} for cash_target_pct"


def compute_cash_floor(equity: float, cash_target_pct: float) -> float:
    if equity <= 0:
        return 0.0
    return equity * cash_target_pct / 100.0
