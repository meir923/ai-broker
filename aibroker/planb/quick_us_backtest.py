"""Plan B — quick daily backtest; delegates to shared engine (fees + slippage from Plan B YAML)."""

from __future__ import annotations

from typing import Any

from aibroker.planb.backtest.engine import run_backtest
from aibroker.planb.config import load_plan_b_config
from aibroker.planb.data.us_bars import load_us_daily_bars
from aibroker.planb.results import backtest_result_to_dict
from aibroker.planb.risk_state import runtime_kill_switch_active
from aibroker.planb.strategies.registry import build_strategy


def run_quick_us_momentum_backtest(
    symbols: list[str], bars: int = 400, *, main_profile: Any = None
) -> dict[str, Any]:
    symbols = [str(s).upper().strip() for s in symbols if str(s).strip()][:5]
    if not symbols:
        symbols = ["SPY"]
    bars = max(60, min(1500, int(bars)))

    mp = None
    if main_profile is not None:
        from pathlib import Path

        mp = Path(main_profile) if not isinstance(main_profile, Path) else main_profile

    cfg = load_plan_b_config(main_profile=mp)
    hist = load_us_daily_bars(symbols, bars=bars)
    sym = max(hist.keys(), key=lambda k: len(hist.get(k) or []))
    bars_s = hist.get(sym) or []

    strat = build_strategy("ma_cross", {"fast": 10, "slow": 30})
    res = run_backtest(
        bars_s,
        strat,
        symbol=sym,
        initial_cash_usd=100_000.0,
        costs=cfg.costs,
        risk=cfg.risk,
        oos=cfg.oos,
        kill_switch_active=runtime_kill_switch_active(),
    )
    out = backtest_result_to_dict(res, bars=bars_s if res.ok else None)
    if not res.ok:
        return {"ok": False, "error": res.error, "symbol": sym, "bars": len(bars_s)}

    oos = res.oos or {}
    return {
        "ok": True,
        "symbol": sym,
        "bars_used": res.bars_used,
        "split_index": oos.get("split_index"),
        "strategy_id": "ma_cross_10_30",
        "strategy_note": "מנוע Plan B משותף (עמלה/סליפג׳ מקונפיג plan_b_us.yaml)",
        "initial_usd": res.initial_cash_usd,
        "return_pct_full_strat": res.return_pct_full,
        "return_pct_full_bh": out.get("return_pct_buy_hold"),
        "return_pct_is_strat": oos.get("return_pct_is"),
        "return_pct_oos_strat": oos.get("return_pct_oos"),
        "final_equity_strat": res.final_equity_usd,
        "final_equity_bh": out.get("buy_hold_final_usd"),
        "max_drawdown_pct": res.max_drawdown_pct,
        "sharpe_annualized": res.sharpe_annualized,
        "trades_sample": out.get("trades", [])[-20:],
        "equity_curve_tail": out.get("equity_curve_tail", []),
    }
