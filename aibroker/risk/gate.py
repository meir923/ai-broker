from __future__ import annotations

from dataclasses import dataclass

from aibroker.brokers.base import OrderIntent
from aibroker.config.schema import AppConfig
from aibroker.state.runtime import RuntimeState


@dataclass
class RiskDecision:
    allowed: bool
    reason: str


def evaluate_intent(
    cfg: AppConfig,
    state: RuntimeState,
    intent: OrderIntent,
    *,
    estimated_notional_usd: float | None = None,
) -> RiskDecision:
    if cfg.risk.kill_switch or state.kill_switch:
        return RiskDecision(False, "kill_switch is active")
    sym = intent.symbol.strip().upper()
    if not sym:
        return RiskDecision(False, "empty symbol")
    if cfg.risk.allowed_symbols and sym not in cfg.risk.allowed_symbols:
        return RiskDecision(False, f"symbol {sym} not in allowed_symbols")
    if state.trades_today >= cfg.risk.max_trades_per_day:
        return RiskDecision(False, "max_trades_per_day reached")
    if state.daily_pnl_usd <= -cfg.risk.max_daily_loss_usd:
        return RiskDecision(False, "max_daily_loss_usd breached")
    if estimated_notional_usd is not None and estimated_notional_usd > cfg.risk.max_notional_per_trade_usd:
        return RiskDecision(False, "max_notional_per_trade_usd exceeded")

    if estimated_notional_usd is not None and state.equity_usd > 0:
        max_exp_pct = cfg.risk.max_position_exposure_pct
        existing = _position_notional(state, sym)
        new_total = existing + estimated_notional_usd
        exposure_pct = new_total / state.equity_usd * 100
        if exposure_pct > max_exp_pct:
            return RiskDecision(False, f"position exposure {exposure_pct:.1f}% > {max_exp_pct}%")

    max_orders = cfg.risk.max_open_orders
    if max_orders > 0 and len(state.open_orders) >= max_orders:
        return RiskDecision(False, f"max_open_orders ({max_orders}) reached")

    return RiskDecision(True, "ok")


def _position_notional(state: RuntimeState, symbol: str) -> float:
    """Sum existing notional for symbol across open positions."""
    total = 0.0
    for p in state.positions:
        if str(p.get("symbol", "")).upper() == symbol:
            total += abs(float(p.get("qty", 0))) * float(p.get("current_price", 0) or p.get("avg_entry_price", 0) or 0)
    return total
