"""Step-based Plan B simulation on daily bars (separate from Plan A paper autopilot)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from aibroker.data.historical import Bar
from aibroker.planb.backtest.engine import (
    TradeRecord,
    _apply_slippage,
    _fee_for_trade,
)
from aibroker.planb.config import PlanBCostsConfig, PlanBConfig, PlanBLLMConfig, PlanBRiskConfig
from aibroker.planb.llm.decision import maybe_llm_signal
from aibroker.planb.risk_state import runtime_kill_switch_active
from aibroker.planb.strategies.base import Strategy, StrategyContext, StrategySignal
from aibroker.planb.strategies.registry import build_strategy

_lock = threading.Lock()
_session: PlanBSimSession | None = None


@dataclass
class PlanBSimSession:
    symbol: str
    bars: list[Bar]
    bar_source: str
    strategy_id: str
    strategy_params: dict[str, Any]
    strategy: Strategy
    costs: PlanBCostsConfig
    risk: PlanBRiskConfig
    llm: PlanBLLMConfig
    initial_cash: float
    cash: float
    shares: float = 0.0
    index: int = 0
    running: bool = True
    decisions: list[dict[str, Any]] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)

    def equity_at_close(self) -> float:
        if not self.bars:
            return self.cash
        i = min(self.index, len(self.bars) - 1)
        c = float(self.bars[i]["c"])
        return self.cash + self.shares * c


def _sym_ok(sym: str, risk: PlanBRiskConfig) -> bool:
    return sym.upper() in risk.allowed_symbols


def planb_sim_start(
    plan_cfg: PlanBConfig,
    *,
    symbol: str,
    bars: int,
    strategy_id: str,
    strategy_params: dict[str, Any] | None,
    initial_cash: float,
    bar_source: str = "daily",
    timeframe_minutes: int = 60,
) -> dict[str, Any]:
    global _session
    from aibroker.planb.data.us_bars import (
        intraday_rows_to_bars,
        load_us_daily_bars,
        load_us_intraday,
    )

    sym = symbol.strip().upper()
    if not _sym_ok(sym, plan_cfg.risk):
        return {"ok": False, "error": f"symbol_not_allowed:{sym}"}

    src = (bar_source or "daily").strip().lower()
    if src == "intraday":
        raw = load_us_intraday(
            [sym],
            timeframe_minutes=max(1, int(timeframe_minutes)),
            limit=max(60, int(bars)),
            prefer_alpaca=True,
        )
        rows = raw.get(sym, [])
        bar_list = intraday_rows_to_bars(rows)
        if len(bar_list) < 35:
            return {
                "ok": False,
                "error": "not_enough_intraday_bars",
                "bars": len(bar_list),
                "hint_he": "נדרשים מפתחות Alpaca + הרשאת נתונים; או שאין מספיק היסטוריה לטווח שנבחר.",
            }
    else:
        hist = load_us_daily_bars([sym], bars=bars)
        bar_list = hist.get(sym) or []
        if len(bar_list) < 35:
            return {"ok": False, "error": "not_enough_bars", "bars": len(bar_list)}

    strat = build_strategy(strategy_id, strategy_params or {})
    start_idx = min(34, max(29, len(bar_list) - 5))
    sess = PlanBSimSession(
        symbol=sym,
        bars=bar_list,
        bar_source=src,
        strategy_id=strategy_id,
        strategy_params=strategy_params or {},
        strategy=strat,
        costs=plan_cfg.costs,
        risk=plan_cfg.risk,
        llm=plan_cfg.llm,
        initial_cash=initial_cash,
        cash=initial_cash,
        shares=0.0,
        index=start_idx,
        running=True,
    )
    with _lock:
        _session = sess
    return {"ok": True, **planb_sim_status()}


def planb_sim_stop() -> dict[str, Any]:
    global _session
    with _lock:
        if _session:
            _session.running = False
        _session = None
    return {"ok": True, "running": False}


def planb_sim_status() -> dict[str, Any]:
    with _lock:
        s = _session
    if not s:
        return {"ok": True, "running": False, "session": None}
    bar = s.bars[s.index] if s.index < len(s.bars) else {}
    return {
        "ok": True,
        "running": s.running,
        "session": {
            "symbol": s.symbol,
            "bar_source": getattr(s, "bar_source", "daily"),
            "index": s.index,
            "total_bars": len(s.bars),
            "date": bar.get("date"),
            "cash": round(s.cash, 2),
            "shares": round(s.shares, 6),
            "equity": round(s.equity_at_close(), 2),
            "strategy": s.strategy.name,
            "decisions_logged": len(s.decisions),
            "trades": len(s.trades),
        },
    }


def planb_sim_step(*, use_llm: bool = False) -> dict[str, Any]:
    s = _session
    if not s or not s.running:
        return {"ok": False, "error": "no_active_session"}
    if s.index >= len(s.bars) - 1:
        s.running = False
        return {"ok": True, "done": True, **planb_sim_status()}

    i = s.index
    bar = s.bars[i]
    date = str(bar.get("date", ""))
    close = float(bar["c"])
    slip = s.costs.slippage_pct

    ctx = StrategyContext(
        bar_index=i, bars=s.bars, position_shares=s.shares, cash_usd=s.cash
    )
    sig, reason = s.strategy.on_bar(ctx)

    if use_llm and s.strategy_id == "llm_rules":
        eq0 = s.cash + s.shares * close
        lsig, lwhy = maybe_llm_signal(
            user_payload={
                "bar": {"date": date, "close": close},
                "position_shares": s.shares,
                "cash": s.cash,
                "equity": eq0,
            },
            risk=s.risk,
            llm=s.llm,
            equity_usd=eq0,
        )
        if lsig != StrategySignal.NONE:
            sig, reason = lsig, lwhy

    if runtime_kill_switch_active() or s.risk.kill_switch:
        sig = StrategySignal.NONE
        reason = "kill_switch"

    max_td = s.risk.max_trades_per_day
    trades_today = sum(1 for t in s.trades if t.date == date)
    if max_td > 0 and trades_today >= max_td:
        sig = StrategySignal.NONE

    if sig == StrategySignal.BUY and s.shares <= 0 and s.cash > 0:
        px = _apply_slippage(close, "buy", slip)
        max_sh = (s.risk.max_notional_per_trade_usd / px) if px > 0 else 0.0
        raw_sh = s.cash / px if px > 0 else 0.0
        buy_sh = min(raw_sh, max_sh)
        if buy_sh * px >= 1.0:
            fee = _fee_for_trade(s.costs, buy_sh, px, "buy")
            cost = buy_sh * px + fee
            if cost <= s.cash:
                s.cash -= cost
                s.shares = buy_sh
                tr = TradeRecord(
                    date=date,
                    side="buy",
                    shares=buy_sh,
                    price=px,
                    fee_usd=fee,
                    reason=reason or "buy",
                )
                s.trades.append(tr)

    elif sig == StrategySignal.SELL and s.shares > 0:
        px = _apply_slippage(close, "sell", slip)
        fee = _fee_for_trade(s.costs, s.shares, px, "sell")
        s.cash += s.shares * px - fee
        tr = TradeRecord(
            date=date,
            side="sell",
            shares=s.shares,
            price=px,
            fee_usd=fee,
            reason=reason or "sell",
        )
        s.shares = 0.0
        s.trades.append(tr)

    eq = s.equity_at_close()
    s.decisions.append(
        {
            "date": date,
            "signal": sig.value,
            "reason": reason,
            "equity": round(eq, 2),
        }
    )

    s.index += 1
    done = s.index >= len(s.bars) - 1
    if done:
        s.running = False

    last = s.decisions[-1] if s.decisions else {}
    return {
        "ok": True,
        "done": done,
        "last": last,
        **planb_sim_status(),
    }
