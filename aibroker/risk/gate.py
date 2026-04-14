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
    return RiskDecision(True, "ok")
