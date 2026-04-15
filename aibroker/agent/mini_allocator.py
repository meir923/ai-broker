"""Thin pre-execution allocator: rank, trim, and drop intents before execution.

Takes normalized intents from Grok's decision and produces a final ordered
list of actionable intents plus a record of what was dropped and why.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aibroker.agent.intent_normalizer import NormalizedIntent
from aibroker.agent.meta_policy import PolicyContext


@dataclass
class AllocationResult:
    final_intents: list[NormalizedIntent] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _intent_sort_key(intent: NormalizedIntent, ctx: PolicyContext) -> tuple[int, int, int]:
    """Lower tuple = higher priority."""
    # Exits/reductions first when posture is defensive
    defensive = ctx.exposure_bias in ("mostly_cash",) or ctx.cash_bias == "raise"
    if intent.reduces_exposure:
        exit_rank = 0 if defensive else 1
    else:
        exit_rank = 2 if defensive else 1

    # Priority symbols get preference
    prio = 0 if intent.symbol in ctx.priority_set else 1

    # Alignment with exposure_bias
    eb = ctx.exposure_bias
    if eb == "net_long" and intent.kind.endswith("_long") and intent.opens_or_increases:
        align = 0
    elif eb == "net_short" and intent.kind.endswith("_short") and intent.opens_or_increases:
        align = 0
    elif intent.reduces_exposure:
        align = 0
    else:
        align = 1

    return (exit_rank, prio, align)


def allocate(
    intents: list[NormalizedIntent],
    ctx: PolicyContext,
    available_cash: float,
    equity: float,
    cash_floor: float,
    price_fn: Any = None,
) -> AllocationResult:
    """Rank, trim, and drop intents according to meta-policy and constraints.

    *price_fn(symbol) -> float* provides estimated execution price.
    """
    result = AllocationResult()
    if not intents:
        return result

    sorted_intents = sorted(intents, key=lambda i: _intent_sort_key(i, ctx))
    remaining_cash = available_cash

    for intent in sorted_intents:
        est_px = price_fn(intent.symbol) if price_fn else 0.0

        if intent.opens_or_increases and intent.side_for_broker == "buy" and est_px > 0:
            cost = intent.final_qty * est_px
            if remaining_cash - cost < cash_floor:
                affordable = max(0, int((remaining_cash - cash_floor) / max(est_px, 1)))
                if affordable <= 0:
                    result.dropped.append({
                        "symbol": intent.symbol,
                        "action": intent.requested_action,
                        "qty": intent.final_qty,
                        "reason": "insufficient cash after cash_floor",
                    })
                    result.notes.append(
                        f"Dropped buy {intent.symbol} x{intent.final_qty} — cash floor breach"
                    )
                    continue
                old_qty = intent.final_qty
                intent.final_qty = affordable
                result.notes.append(
                    f"Trimmed buy {intent.symbol} from {old_qty} to {affordable} — cash floor"
                )
            remaining_cash -= intent.final_qty * est_px

        result.final_intents.append(intent)

    return result
