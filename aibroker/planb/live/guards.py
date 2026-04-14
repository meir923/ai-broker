"""Live trading gates for Plan B (separate from Plan A `I_ACCEPT_LIVE_RISK`)."""

from __future__ import annotations

import os

# Distinct env so enabling Plan A live does not implicitly enable Plan B.
PLAN_B_LIVE_ACCEPT = "I_ACCEPT_PLAN_B_LIVE_RISK"
ACCEPT_VALUE = "true"


def require_live_accept_env() -> None:
    if os.environ.get(PLAN_B_LIVE_ACCEPT, "").strip().lower() != ACCEPT_VALUE:
        raise ValueError(
            f"Plan B live requires {PLAN_B_LIVE_ACCEPT}={ACCEPT_VALUE} in the environment."
        )


def live_execution_enabled_in_env() -> bool:
    return os.environ.get(PLAN_B_LIVE_ACCEPT, "").strip().lower() == ACCEPT_VALUE


def live_execution_allowed(*, plan_b_live_enabled: bool, account_mode_live: bool) -> dict:
    """Return whether Alpaca live (not paper) orders would be permitted."""
    if not plan_b_live_enabled:
        return {"allowed": False, "reason": "plan_b.live.enabled is false in plan_b_us.yaml"}
    if not account_mode_live:
        return {"allowed": False, "reason": "main profile account_mode is not live"}
    if not live_execution_enabled_in_env():
        return {
            "allowed": False,
            "reason": f"missing {PLAN_B_LIVE_ACCEPT}={ACCEPT_VALUE}",
        }
    return {"allowed": True, "reason": "ok"}
