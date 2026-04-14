from __future__ import annotations

from dataclasses import asdict
from typing import Any

from aibroker.data.historical import Bar
from aibroker.planb.backtest.engine import BacktestResult


def backtest_result_to_dict(result: BacktestResult, *, bars: list[Bar] | None = None) -> dict[str, Any]:
    """JSON-friendly dict; optional `bars` adds buy-and-hold benchmark."""
    base: dict[str, Any] = {
        "ok": result.ok,
        "symbol": result.symbol,
        "strategy_name": result.strategy_name,
        "bars_used": result.bars_used,
        "initial_cash_usd": result.initial_cash_usd,
        "final_equity_usd": result.final_equity_usd,
        "return_pct_full": result.return_pct_full,
        "max_drawdown_pct": result.max_drawdown_pct,
        "sharpe_annualized": result.sharpe_annualized,
        "trades": [asdict(t) for t in result.trades[-300:]],
        "equity_curve_tail": result.equity_curve[-120:],
        "oos": result.oos,
        "error": result.error,
    }
    if bars and result.ok and len(bars) > 1 and result.initial_cash_usd > 0:
        first = float(bars[0]["c"])
        last = float(bars[-1]["c"])
        bh_final = result.initial_cash_usd / first * last
        base["buy_hold_final_usd"] = round(bh_final, 2)
        base["return_pct_buy_hold"] = round(
            (bh_final / result.initial_cash_usd - 1.0) * 100.0, 2
        )
    return base
