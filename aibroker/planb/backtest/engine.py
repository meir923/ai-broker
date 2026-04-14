from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from aibroker.data.historical import Bar
from aibroker.planb.config import PlanBCostsConfig, PlanBOOSConfig, PlanBRiskConfig
from aibroker.planb.strategies.base import Strategy, StrategyContext, StrategySignal


@dataclass
class TradeRecord:
    date: str
    side: str
    shares: float
    price: float
    fee_usd: float
    reason: str


@dataclass
class BacktestResult:
    ok: bool
    symbol: str
    strategy_name: str
    bars_used: int
    initial_cash_usd: float
    final_equity_usd: float
    return_pct_full: float
    max_drawdown_pct: float
    sharpe_annualized: float | None
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    oos: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _fee_for_trade(
    costs: PlanBCostsConfig, shares: float, fill_price: float, side: str
) -> float:
    sh = abs(shares)
    notional = sh * fill_price
    return costs.fee_per_share_usd * sh + costs.fee_pct_of_notional * notional


def _apply_slippage(price: float, side: str, slip: float) -> float:
    if side == "buy":
        return price * (1.0 + slip)
    return price * (1.0 - slip)


def _max_drawdown_pct(equities: list[float]) -> float:
    if not equities:
        return 0.0
    peak = equities[0]
    max_dd = 0.0
    for x in equities:
        if x > peak:
            peak = x
        if peak > 0:
            dd = (peak - x) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return round(max_dd, 2)


def _sharpe_annualized(daily_returns: list[float]) -> float | None:
    if len(daily_returns) < 5:
        return None
    m = sum(daily_returns) / len(daily_returns)
    var = sum((x - m) ** 2 for x in daily_returns) / max(1, len(daily_returns) - 1)
    sd = math.sqrt(var)
    if sd < 1e-12:
        return None
    return round((m / sd) * math.sqrt(252.0), 3)


def run_backtest(
    bars: list[Bar],
    strategy: Strategy,
    *,
    symbol: str,
    initial_cash_usd: float,
    costs: PlanBCostsConfig,
    risk: PlanBRiskConfig,
    oos: PlanBOOSConfig,
    kill_switch_active: bool = False,
) -> BacktestResult:
    n = len(bars)
    if n < 30:
        return BacktestResult(
            ok=False,
            symbol=symbol,
            strategy_name=strategy.name,
            bars_used=n,
            initial_cash_usd=initial_cash_usd,
            final_equity_usd=initial_cash_usd,
            return_pct_full=0.0,
            max_drawdown_pct=0.0,
            sharpe_annualized=None,
            error="not_enough_bars",
        )

    strategy.reset()
    cash = initial_cash_usd
    shares = 0.0
    trades: list[TradeRecord] = []
    equity_curve: list[dict[str, Any]] = []
    equities: list[float] = []
    daily_last: dict[str, float] = {}
    daily_returns: list[float] = []
    trades_today: dict[str, int] = {}
    day_start_equity: dict[str, float] = {}
    loss_halt_date: str | None = None
    prev_date: str | None = None

    split = max(30, int(n * oos.train_fraction)) if oos.mode == "holdout_end" else n
    strat_at_split: float | None = None

    slip = costs.slippage_pct
    max_td = risk.max_trades_per_day

    for i in range(n):
        bar = bars[i]
        date = str(bar.get("date", ""))
        close = float(bar["c"])

        if date and date != prev_date:
            day_start_equity[date] = cash + shares * close
            trades_today[date] = 0
            prev_date = date
            if loss_halt_date and date > loss_halt_date:
                loss_halt_date = None

        halted = bool(loss_halt_date and date == loss_halt_date)

        ctx = StrategyContext(
            bar_index=i, bars=bars, position_shares=shares, cash_usd=cash
        )
        sig, reason = strategy.on_bar(ctx)

        if kill_switch_active or risk.kill_switch or halted:
            sig = StrategySignal.NONE

        at_trade_limit = max_td > 0 and trades_today.get(date, 0) >= max_td
        if at_trade_limit:
            sig = StrategySignal.NONE

        if sig == StrategySignal.BUY and shares <= 0 and cash > 0:
            px = _apply_slippage(close, "buy", slip)
            max_sh = (risk.max_notional_per_trade_usd / px) if px > 0 else 0.0
            raw_sh = cash / px if px > 0 else 0.0
            buy_sh = min(raw_sh, max_sh)
            if buy_sh * px >= 1.0:
                fee = _fee_for_trade(costs, buy_sh, px, "buy")
                cost = buy_sh * px + fee
                if cost <= cash:
                    cash -= cost
                    shares = buy_sh
                    trades.append(
                        TradeRecord(
                            date=date,
                            side="buy",
                            shares=buy_sh,
                            price=px,
                            fee_usd=fee,
                            reason=reason or "buy",
                        )
                    )
                    trades_today[date] = trades_today.get(date, 0) + 1

        elif sig == StrategySignal.SELL and shares > 0:
            px = _apply_slippage(close, "sell", slip)
            fee = _fee_for_trade(costs, shares, px, "sell")
            cash += shares * px - fee
            trades.append(
                TradeRecord(
                    date=date,
                    side="sell",
                    shares=shares,
                    price=px,
                    fee_usd=fee,
                    reason=reason or "sell",
                )
            )
            shares = 0.0
            trades_today[date] = trades_today.get(date, 0) + 1

        eq = cash + shares * close
        if date and risk.max_daily_loss_usd > 0:
            start_eq = day_start_equity.get(date, eq)
            if start_eq - eq >= risk.max_daily_loss_usd:
                if shares > 0:
                    px = _apply_slippage(close, "sell", slip)
                    fee = _fee_for_trade(costs, shares, px, "sell")
                    cash += shares * px - fee
                    trades.append(
                        TradeRecord(
                            date=date,
                            side="sell",
                            shares=shares,
                            price=px,
                            fee_usd=fee,
                            reason="risk_daily_loss_flat",
                        )
                    )
                    shares = 0.0
                loss_halt_date = date
                eq = cash + shares * close

        equities.append(eq)
        equity_curve.append({"date": date, "equity": round(eq, 2)})
        _update_daily_returns(daily_last, daily_returns, date, eq)
        if i == split - 1:
            strat_at_split = eq

    final_c = float(bars[-1]["c"])
    final_eq = cash + shares * final_c
    ret_full = (final_eq / initial_cash_usd - 1.0) * 100.0 if initial_cash_usd else 0.0

    oos_payload: dict[str, Any] = {"mode": oos.mode}
    if oos.mode == "holdout_end" and strat_at_split is not None and split < n:
        oos_payload["split_index"] = split
        oos_payload["return_pct_is"] = round(
            (strat_at_split / initial_cash_usd - 1.0) * 100.0, 2
        )
        oos_payload["return_pct_oos"] = round(
            (final_eq / strat_at_split - 1.0) * 100.0, 2
        )

    return BacktestResult(
        ok=True,
        symbol=symbol,
        strategy_name=strategy.name,
        bars_used=n,
        initial_cash_usd=initial_cash_usd,
        final_equity_usd=round(final_eq, 2),
        return_pct_full=round(ret_full, 2),
        max_drawdown_pct=_max_drawdown_pct(equities),
        sharpe_annualized=_sharpe_annualized(daily_returns),
        trades=trades,
        equity_curve=equity_curve[-500:],
        oos=oos_payload,
    )


def _update_daily_returns(
    daily_last: dict[str, float],
    daily_returns: list[float],
    date: str,
    eq: float,
) -> None:
    if not date:
        return
    if date not in daily_last:
        if daily_last:
            prev_eq = next(reversed(daily_last.values()))
            if prev_eq > 0:
                daily_returns.append(eq / prev_eq - 1.0)
    daily_last[date] = eq
