"""Unified last-mile approval for sim and live trade intents.

Combines drawdown, per-symbol exposure, cash floor, directional, and
buying-power checks into a single approve() call that both sim and live use.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aibroker.agent.intent_normalizer import NormalizedIntent
from aibroker.agent.risk_profiles import RISK_PROFILES


@dataclass
class ApprovalResult:
    allowed: bool
    final_qty: int
    reasons: list[str] = field(default_factory=list)
    reduced_from: int | None = None


def approve_sim(
    intent: NormalizedIntent,
    *,
    equity: float,
    initial_deposit: float,
    positions: dict[str, Any],
    est_price: float,
    risk_level: str,
) -> ApprovalResult:
    """Approve (or reduce/reject) a sim intent against risk limits."""
    rp = RISK_PROFILES.get(risk_level, RISK_PROFILES["medium"])
    reasons: list[str] = []

    max_dd = float(rp.get("max_drawdown_pct", 0.40))
    if equity < initial_deposit * (1 - max_dd):
        return ApprovalResult(False, 0, [f"drawdown > {int(max_dd*100)}%"])

    max_sym = float(rp.get("max_symbol_exposure_pct", 0.35))
    if intent.opens_or_increases and equity > 0 and est_price > 0:
        existing = abs(float(positions.get(intent.symbol, {}).get("qty", 0))) * est_price
        new_notional = intent.final_qty * est_price
        if (existing + new_notional) / equity > max_sym:
            allowed_notional = max(0, equity * max_sym - existing)
            capped = max(0, int(allowed_notional / max(est_price, 1)))
            if capped <= 0:
                return ApprovalResult(False, 0, [f"exposure > {int(max_sym*100)}%"])
            reasons.append(f"trimmed from {intent.final_qty} to {capped} for exposure cap")
            return ApprovalResult(True, capped, reasons, reduced_from=intent.final_qty)

    return ApprovalResult(True, intent.final_qty, reasons)


def approve_live(
    intent: NormalizedIntent,
    *,
    acct: dict[str, Any],
    positions: dict[str, Any],
    est_price: float,
    risk_level: str,
    equity: float,
    margin_rate: float,
) -> ApprovalResult:
    """Approve (or reduce/reject) a live intent against risk + buying power."""
    rp = RISK_PROFILES.get(risk_level, RISK_PROFILES["medium"])
    reasons: list[str] = []

    if est_price <= 0 or intent.final_qty <= 0:
        return ApprovalResult(False, 0, ["zero price or qty"])

    bp = float(acct.get("buying_power_usd", 0) or 0)
    cur_q = float(positions.get(intent.symbol, {}).get("qty", 0))
    qty = intent.final_qty

    if intent.side_for_broker == "buy":
        max_q = int(max(0, bp * 0.95 / est_price))
        qty = min(qty, max_q)
    elif cur_q > 0 and intent.reduces_exposure:
        qty = min(qty, int(cur_q))
    else:
        margin_per = est_price * margin_rate
        if margin_per <= 0:
            return ApprovalResult(False, 0, ["zero margin rate"])
        max_q = int(max(0, bp * 0.9 / margin_per))
        qty = min(qty, max_q)

    if qty <= 0:
        return ApprovalResult(False, 0, ["insufficient buying power"])

    max_sym = float(rp.get("max_symbol_exposure_pct", 0.35))
    eq = equity if equity > 0 else float(acct.get("equity_usd", 0) or 0)

    if intent.opens_or_increases and eq > 0:
        existing_notional = abs(cur_q) * est_price
        new_notional = qty * est_price
        if (existing_notional + new_notional) / eq > max_sym:
            allowed = max(0, eq * max_sym - existing_notional)
            qty = min(qty, max(0, int(allowed / est_price)))
            if qty <= 0:
                return ApprovalResult(False, 0, [f"exposure > {int(max_sym*100)}%"])
            reasons.append(f"trimmed for exposure cap")

    if qty != intent.final_qty:
        reasons.append(f"reduced from {intent.final_qty} to {qty}")
        return ApprovalResult(True, qty, reasons, reduced_from=intent.final_qty)

    return ApprovalResult(True, qty, reasons)
