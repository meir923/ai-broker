"""Normalize raw Grok actions into semantic trade intents.

Eliminates ambiguity around "sell" (sell-to-close-long vs sell-to-open-short)
and provides a clean interface for downstream risk/approval logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


INTENT_KINDS = (
    "open_long", "add_long", "reduce_long", "close_long",
    "open_short", "add_short", "reduce_short", "close_short",
)


@dataclass
class NormalizedIntent:
    symbol: str
    requested_action: str       # original: buy / sell / short / cover
    kind: str                   # one of INTENT_KINDS
    side_for_broker: str        # buy or sell (broker-level)
    requested_qty: int
    final_qty: int              # may be adjusted later by allocator/approval
    current_qty: float          # position qty at decision time
    reason: str
    opens_or_increases: bool    # True when exposure grows
    reduces_exposure: bool      # True when exposure shrinks


def normalize(
    action: str,
    symbol: str,
    qty: int,
    current_qty: float,
    reason: str = "",
) -> NormalizedIntent:
    """Map a raw Grok action + current position into a NormalizedIntent."""
    action = action.lower()
    sym = symbol.upper()
    cur = float(current_qty)

    if action == "buy":
        if cur < 0:
            kind = "reduce_short" if qty < abs(cur) else "close_short"
            return NormalizedIntent(
                symbol=sym, requested_action=action, kind=kind,
                side_for_broker="buy", requested_qty=qty, final_qty=qty,
                current_qty=cur, reason=reason,
                opens_or_increases=False, reduces_exposure=True,
            )
        kind = "open_long" if cur == 0 else "add_long"
        return NormalizedIntent(
            symbol=sym, requested_action=action, kind=kind,
            side_for_broker="buy", requested_qty=qty, final_qty=qty,
            current_qty=cur, reason=reason,
            opens_or_increases=True, reduces_exposure=False,
        )

    if action == "sell":
        if cur > 0:
            kind = "close_long" if qty >= cur else "reduce_long"
            return NormalizedIntent(
                symbol=sym, requested_action=action, kind=kind,
                side_for_broker="sell", requested_qty=qty, final_qty=qty,
                current_qty=cur, reason=reason,
                opens_or_increases=False, reduces_exposure=True,
            )
        kind = "open_short" if cur == 0 else "add_short"
        return NormalizedIntent(
            symbol=sym, requested_action=action, kind=kind,
            side_for_broker="sell", requested_qty=qty, final_qty=qty,
            current_qty=cur, reason=reason,
            opens_or_increases=True, reduces_exposure=False,
        )

    if action == "short":
        kind = "open_short" if cur >= 0 else "add_short"
        return NormalizedIntent(
            symbol=sym, requested_action=action, kind=kind,
            side_for_broker="sell", requested_qty=qty, final_qty=qty,
            current_qty=cur, reason=reason,
            opens_or_increases=True, reduces_exposure=False,
        )

    if action == "cover":
        if cur >= 0:
            return NormalizedIntent(
                symbol=sym, requested_action=action, kind="close_short",
                side_for_broker="buy", requested_qty=qty, final_qty=qty,
                current_qty=cur, reason=reason,
                opens_or_increases=False, reduces_exposure=cur < 0,
            )
        kind = "close_short" if qty >= abs(cur) else "reduce_short"
        return NormalizedIntent(
            symbol=sym, requested_action=action, kind=kind,
            side_for_broker="buy", requested_qty=qty, final_qty=qty,
            current_qty=cur, reason=reason,
            opens_or_increases=False, reduces_exposure=True,
        )

    raise ValueError(f"Unknown action: {action!r}")
