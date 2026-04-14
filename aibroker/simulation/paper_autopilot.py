"""
Swing-trading backtest walker.

Each server tick advances one **daily bar** through historical data.
Strategies evaluate every symbol every bar; positions have ATR-based
stop-loss and take-profit orders checked against intrabar highs/lows.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aibroker.brokers.base import OrderIntent
from aibroker.config.loader import load_profile
from aibroker.config.schema import AppConfig
from aibroker.simulation.demo_trades import _build_row
from aibroker.state.runtime import RuntimeState

log = logging.getLogger(__name__)

_profile_path: Path | None = None
_lock = threading.Lock()
_session: PaperSession | None = None
_worker_stop = threading.Event()
_worker_thread: threading.Thread | None = None
_latest_sentiment: dict[str, float] = {}

WARMUP_BARS = 50

COMMISSION_PER_SHARE = 0.005
MIN_COMMISSION = 1.00
SLIPPAGE_PCT = 0.0005


def _apply_slippage(px: float, side: str) -> float:
    """Pessimistic fill: buy higher, sell lower."""
    if side == "buy":
        return px * (1.0 + SLIPPAGE_PCT)
    return px * (1.0 - SLIPPAGE_PCT)


def _calc_commission(qty: float) -> float:
    return max(MIN_COMMISSION, abs(qty) * COMMISSION_PER_SHARE)


@dataclass
class ClosedTrade:
    symbol: str
    side: str
    entry_px: float
    exit_px: float
    qty: float
    pnl: float
    r_multiple: float
    strategy: str
    entry_bar: int
    exit_bar: int


@dataclass
class PaperSession:
    """Mutable in-memory session (resets on server restart)."""

    running: bool = False
    initial_deposit_usd: float = 0.0
    cash_usd: float = 0.0
    positions: dict[str, dict[str, float]] = field(default_factory=dict)
    filled_trades: int = 0
    tick_count: int = 0
    trade_seq: int = 0
    interval_sec: float = 3.0
    started_at_utc: str | None = None
    last_tick_at_utc: str | None = None
    last_error: str | None = None
    trades: list[dict[str, Any]] = field(default_factory=list)
    profile_name: str = ""
    _next_step_mono: float = field(default=0.0, repr=False)

    bar_index: int = WARMUP_BARS
    initial_bar_index: int = WARMUP_BARS
    history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    leverage: float = 2.0
    pending_entries: list[tuple[Any, str, dict]] = field(default_factory=list)
    live_mode: bool = False

    def max_trades_cap(self) -> int:
        return 50_000

    @property
    def current_date(self) -> str:
        for bars in self.history.values():
            if self.bar_index < len(bars):
                return bars[self.bar_index].get("date", "")
        return ""

    @property
    def days_simulated(self) -> int:
        return max(0, self.bar_index - self.initial_bar_index)

    @property
    def win_rate(self) -> float:
        if not self.closed_trades:
            return 0.0
        wins = sum(1 for t in self.closed_trades if t.pnl > 0)
        return round(wins / len(self.closed_trades) * 100, 1)

    @property
    def avg_r(self) -> float:
        if not self.closed_trades:
            return 0.0
        return round(sum(t.r_multiple for t in self.closed_trades) / len(self.closed_trades), 2)

    @property
    def avg_win(self) -> float:
        wins = [t.pnl for t in self.closed_trades if t.pnl > 0]
        return round(sum(wins) / len(wins), 2) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [t.pnl for t in self.closed_trades if t.pnl <= 0]
        return round(sum(losses) / len(losses), 2) if losses else 0.0


def configure_paper_autopilot(profile_path: Path) -> None:
    global _profile_path
    _profile_path = profile_path.resolve()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mark_prices(session: PaperSession) -> dict[str, float]:
    """Current bar's close for every symbol — single source of truth."""
    prices: dict[str, float] = {}
    for sym, bars in session.history.items():
        idx = min(session.bar_index, len(bars) - 1)
        prices[sym] = float(bars[idx]["c"])
    return prices


def _open_prices(session: PaperSession) -> dict[str, float]:
    """Current bar's open for every symbol — used for filling pending entries."""
    prices: dict[str, float] = {}
    for sym, bars in session.history.items():
        idx = min(session.bar_index, len(bars) - 1)
        prices[sym] = float(bars[idx]["o"])
    return prices


def _equity(session: PaperSession, mark: dict[str, float]) -> float:
    eq = session.cash_usd
    for sym, pos in session.positions.items():
        q = float(pos.get("qty", 0.0))
        if abs(q) > 1e-9:
            px = mark.get(sym.upper(), float(pos.get("avg_px", 0.0)))
            avg_px = float(pos.get("avg_px", 0.0))
            pos_lev = float(pos.get("lev", session.leverage))
            margin_held = abs(q) * avg_px / pos_lev
            if q > 0:
                pnl = (px - avg_px) * abs(q)
            else:
                pnl = (avg_px - px) * abs(q)
            eq += margin_held + pnl
    return eq


def _runtime_for_gate(cfg: AppConfig, session: PaperSession, mark: dict[str, float]) -> RuntimeState:
    equity = _equity(session, mark)
    pnl = equity - session.initial_deposit_usd
    plist: list[dict[str, Any]] = []
    for sym, pos in session.positions.items():
        q = float(pos.get("qty", 0.0))
        if abs(q) > 1e-9:
            plist.append({"symbol": sym.upper(), "qty": q, "avg_cost_usd": float(pos.get("avg_px", 0.0))})
    return RuntimeState(
        profile_name=cfg.profile_name,
        account_mode=cfg.account_mode,
        dry_run=True,
        kill_switch=cfg.risk.kill_switch,
        trades_today=session.filled_trades,
        daily_pnl_usd=pnl,
        positions=plist,
    )


_portfolio_mgr: Any = None


def _get_manager():
    global _portfolio_mgr
    if _portfolio_mgr is None:
        from aibroker.strategies.simple_rules import SwingPortfolioManager
        _portfolio_mgr = SwingPortfolioManager()
    return _portfolio_mgr


def _apply_buy(session: PaperSession, sym: str, qty: float, px: float, leverage: float = 0) -> bool:
    """Buy shares: opens/adds to long, or covers short. Includes slippage + commission."""
    if qty <= 0 or px <= 0 or not math.isfinite(qty) or not math.isfinite(px):
        return False
    px = _apply_slippage(px, "buy")
    commission = _calc_commission(qty)
    lev = leverage if leverage > 0 else session.leverage
    sym_u = sym.upper()
    p = session.positions.setdefault(sym_u, {"qty": 0.0, "avg_px": 0.0, "lev": lev})
    old_q = float(p["qty"])
    avg_px = float(p.get("avg_px", 0.0))

    if old_q < -1e-9:
        cover_qty = min(qty, abs(old_q))
        margin_back = cover_qty * avg_px / lev
        pnl = (avg_px - px) * cover_qty
        session.cash_usd += margin_back + pnl - commission
        remaining = qty - cover_qty
        if remaining > 1e-9:
            margin_needed = remaining * px / lev
            if margin_needed + commission > session.cash_usd + 1e-6:
                return False
            session.cash_usd -= margin_needed
    else:
        margin_needed = qty * px / lev
        if margin_needed + commission > session.cash_usd + 1e-6:
            return False
        session.cash_usd -= margin_needed + commission

    new_q = old_q + qty
    if new_q > 1e-9:
        if old_q > 1e-9:
            p["avg_px"] = (avg_px * old_q + px * qty) / new_q
        else:
            p["avg_px"] = px
    p["qty"] = new_q
    if abs(p["qty"]) < 1e-9:
        del session.positions[sym_u]
    return True


def _apply_sell(session: PaperSession, sym: str, qty: float, px: float, leverage: float = 0) -> bool:
    """Sell shares: closes/reduces long, or opens/adds to short. Includes slippage + commission."""
    if qty <= 0 or px <= 0 or not math.isfinite(qty) or not math.isfinite(px):
        return False
    px = _apply_slippage(px, "sell")
    commission = _calc_commission(qty)
    lev = leverage if leverage > 0 else session.leverage
    sym_u = sym.upper()
    p = session.positions.setdefault(sym_u, {"qty": 0.0, "avg_px": 0.0, "lev": lev})
    old_q = float(p["qty"])
    avg_px = float(p.get("avg_px", 0.0))

    if old_q > 1e-9:
        close_qty = min(qty, old_q)
        margin_back = close_qty * avg_px / lev
        pnl = (px - avg_px) * close_qty
        session.cash_usd += margin_back + pnl - commission
        remaining = qty - close_qty
        if remaining > 1e-9:
            margin_needed = remaining * px / lev
            if margin_needed + commission > session.cash_usd + 1e-6:
                return False
            session.cash_usd -= margin_needed
    else:
        margin_needed = qty * px / lev
        if margin_needed + commission > session.cash_usd + 1e-6:
            return False
        session.cash_usd -= margin_needed + commission

    new_q = old_q - qty
    if new_q < -1e-9:
        if old_q < -1e-9:
            p["avg_px"] = (avg_px * abs(old_q) + px * qty) / abs(new_q)
        else:
            p["avg_px"] = px
    p["qty"] = new_q
    if abs(p["qty"]) < 1e-9:
        del session.positions[sym_u]
    return True


def _record_closed_trade(
    session: PaperSession,
    sym: str,
    side: str,
    entry_px: float,
    exit_px: float,
    qty: float,
    strategy: str,
    entry_bar: int,
    initial_stop: float = 0.0,
) -> None:
    if side == "long":
        pnl = (exit_px - entry_px) * qty
    else:
        pnl = (entry_px - exit_px) * qty
    risk_per_share = abs(entry_px - initial_stop) if abs(initial_stop) > 1e-6 else entry_px * 0.02
    risk_abs = max(risk_per_share * qty, 0.01)
    r_mult = pnl / risk_abs
    session.closed_trades.append(ClosedTrade(
        symbol=sym, side=side, entry_px=entry_px, exit_px=exit_px,
        qty=qty, pnl=round(pnl, 2), r_multiple=round(r_mult, 2),
        strategy=strategy, entry_bar=entry_bar, exit_bar=session.bar_index,
    ))


def _close_all_positions(session: PaperSession) -> None:
    """Close all open positions at current market prices (end of backtest)."""
    mark = _mark_prices(session)
    mgr = _get_manager()
    for sym in list(session.positions.keys()):
        p = session.positions.get(sym)
        if not p:
            continue
        q = float(p.get("qty", 0))
        if abs(q) < 1e-9:
            continue
        px = mark.get(sym, float(p.get("avg_px", 0)))
        entry_px = float(p.get("avg_px", 0))
        meta = mgr.get_meta(sym)
        side = "long" if q > 0 else "short"
        _record_closed_trade(
            session, sym, side, entry_px, px, abs(q),
            meta.get("strategy", "end_of_backtest"),
            meta.get("entry_bar", session.bar_index),
            initial_stop=meta.get("initial_stop", 0),
        )
        pos_lev = float(p.get("lev", session.leverage))
        margin_held = abs(q) * entry_px / pos_lev
        if q > 0:
            pnl = (px - entry_px) * abs(q)
        else:
            pnl = (entry_px - px) * abs(q)
        session.cash_usd += margin_held + pnl
    session.positions.clear()


def _fill_pending_entries(cfg: AppConfig, session: PaperSession) -> None:
    """Fill pending entries from the previous bar at the current bar's open price."""
    if not session.pending_entries:
        return
    opens = _open_prices(session)
    state = _runtime_for_gate(cfg, session, opens)
    cur_date = session.current_date
    mgr = _get_manager()

    filled: list[tuple[Any, str, dict]] = []
    for intent, strat_name, extra in session.pending_entries:
        if session.filled_trades >= session.max_trades_cap():
            break
        sym = intent.symbol.upper()
        qty = float(intent.quantity)
        side = intent.side
        px = float(opens.get(sym, 0))
        if px <= 0:
            continue

        meta = mgr.get_meta(sym)
        sym_lev = meta.get("symbol_leverage", session.leverage)
        margin_needed = qty * px / sym_lev
        if margin_needed > session.cash_usd + 1e-6:
            buying_power = session.cash_usd * sym_lev
            adjusted_qty = max(1, math.floor(buying_power / px))
            if adjusted_qty < 1 or adjusted_qty * px / sym_lev > session.cash_usd + 1e-6:
                continue
            qty = float(adjusted_qty)

        held = float(session.positions.get(sym, {}).get("qty", 0.0))
        pos_label = extra.get("pos_label", "LONG")

        session.trade_seq += 1
        note = f"אסטרטגיה: {strat_name} · {pos_label} · יום: {cur_date} (open fill)"
        row = _build_row(
            cfg, state, side=side, sym=sym, qty=qty, ref_px=px,
            strategy_source=strat_name.split("|")[0],
            strategy_note=note,
            time_offset_ms=0,
            trade_seq=session.trade_seq,
        )
        row["position_type"] = pos_label
        row["sim_date"] = cur_date
        row["stop"] = extra.get("stop")
        row["target"] = extra.get("target")
        row["fill_type"] = "next_bar_open"

        if row.get("execution") == "DRY_RUN_RECORDED":
            trade_lev = meta.get("symbol_leverage", session.leverage)
            ok = _apply_buy(session, sym, qty, px, trade_lev) if side == "buy" else _apply_sell(session, sym, qty, px, trade_lev)
            if ok:
                session.filled_trades += 1
                mgr.record_open_fill_price(sym, px)
            else:
                row["execution"] = "BLOCKED_BY_RISK"
                row["message"] = "חסום (נייר): מזומן/מרג'ין לא מספיקים"
                row["risk_ok"] = False

        session.trades.append(row)

    session.pending_entries.clear()


def _step_once(cfg: AppConfig, session: PaperSession) -> None:
    """Advance one daily bar and run all strategies."""
    max_bar = min(len(b) for b in session.history.values()) if session.history else 0
    if session.bar_index >= max_bar - 1:
        _close_all_positions(session)
        session.running = False
        session.last_error = None
        log.info("Backtest complete: %d days, %d closed trades, equity $%.2f",
                 session.days_simulated, len(session.closed_trades),
                 session.cash_usd)
        return

    session.bar_index += 1
    session.tick_count += 1

    _fill_pending_entries(cfg, session)

    mark = _mark_prices(session)
    equity = _equity(session, mark)
    symbols = list(session.history.keys())

    mgr = _get_manager()
    sent = _latest_sentiment if session.live_mode else {}
    intents = mgr.evaluate_all(
        bar_idx=session.bar_index,
        history=session.history,
        positions=session.positions,
        equity=equity,
        symbols=symbols,
        leverage=session.leverage,
        cash=session.cash_usd,
        sentiment=sent,
    )

    session.last_error = None
    if not intents:
        session.last_tick_at_utc = _utc_now_iso()
        return

    state = _runtime_for_gate(cfg, session, mark)
    cur_date = session.current_date

    for batch_idx, (intent, strat_name) in enumerate(intents):
        if session.filled_trades >= session.max_trades_cap():
            break
        sym = intent.symbol.upper()
        qty = float(intent.quantity)
        side = intent.side

        meta = mgr.get_meta(sym)
        exit_px_override = meta.get("exit_px", 0)
        is_pyramid = strat_name.endswith("|pyramid")
        is_exit = "|" in strat_name and not is_pyramid

        if is_exit:
            px = exit_px_override if exit_px_override > 0 else float(mark.get(sym, 0))
            if px <= 0:
                continue
            held = float(session.positions.get(sym, {}).get("qty", 0.0))
            avg_px = float(session.positions.get(sym, {}).get("avg_px", 0.0))

            pos_label = "COVER" if (side == "buy" and held < -1e-9) else "CLOSE"
            stop_px = meta.get("stop", 0)
            target_px = meta.get("target", 0)

            session.trade_seq += 1
            note = f"אסטרטגיה: {strat_name} · {pos_label} · יום: {cur_date}"
            row = _build_row(
                cfg, state, side=side, sym=sym, qty=qty, ref_px=px,
                strategy_source=strat_name.split("|")[0],
                strategy_note=note,
                time_offset_ms=batch_idx * 80,
                trade_seq=session.trade_seq,
            )
            row["position_type"] = pos_label
            row["sim_date"] = cur_date
            row["stop"] = round(stop_px, 2) if stop_px else None
            row["target"] = round(target_px, 2) if target_px else None

            if row.get("execution") == "DRY_RUN_RECORDED":
                if abs(held) > 1e-9:
                    entry_side = "long" if held > 0 else "short"
                    _record_closed_trade(
                        session, sym, entry_side, avg_px, px, abs(held),
                        strat_name.split("|")[0],
                        meta.get("entry_bar", session.bar_index),
                        initial_stop=meta.get("initial_stop", 0),
                    )
                trade_lev = meta.get("symbol_leverage", session.leverage)
                ok = _apply_buy(session, sym, qty, px, trade_lev) if side == "buy" else _apply_sell(session, sym, qty, px, trade_lev)
                if ok:
                    session.filled_trades += 1
                else:
                    row["execution"] = "BLOCKED_BY_RISK"
                    row["message"] = "חסום (נייר): מזומן/מרג'ין לא מספיקים"
                    row["risk_ok"] = False

            session.trades.append(row)
        else:
            px = float(mark.get(sym, 0))
            if px <= 0:
                continue
            held = float(session.positions.get(sym, {}).get("qty", 0.0))
            if is_pyramid:
                pos_label = "PYRAMID"
            elif side == "buy":
                pos_label = "LONG"
            else:
                pos_label = "SHORT"

            stop_px = meta.get("stop", 0)
            target_px = meta.get("target", 0)

            session.pending_entries.append((
                intent, strat_name,
                {
                    "pos_label": pos_label,
                    "stop": round(stop_px, 2) if stop_px else None,
                    "target": round(target_px, 2) if target_px else None,
                },
            ))

    if len(session.trades) > 2000:
        del session.trades[:-2000]
    session.last_tick_at_utc = _utc_now_iso()


def paper_tick(cfg: AppConfig) -> None:
    global _session
    with _lock:
        s = _session
        if s is None or not s.running:
            return
        now = time.monotonic()
        if now < s._next_step_mono:
            return
        if s.profile_name and s.profile_name != cfg.profile_name:
            s.last_error = "profile_mismatch — עצור והתחל מחדש אחרי החלפת פרופיל"
            s._next_step_mono = now + float(s.interval_sec)
            return
        try:
            _step_once(cfg, s)
        except Exception as e:
            log.exception("paper step failed")
            s.last_error = str(e)
        s._next_step_mono = now + float(s.interval_sec)


def paper_start(
    cfg: AppConfig,
    *,
    deposit_usd: float,
    interval_sec: float = 3.0,
    leverage: float = 2.0,
    start_date: str | None = None,
) -> dict[str, Any]:
    global _session, _portfolio_mgr
    if not math.isfinite(deposit_usd) or not math.isfinite(interval_sec):
        return {"ok": False, "error": "deposit_usd and interval_sec must be finite numbers"}
    if deposit_usd < 100 or deposit_usd > 1_000_000_000:
        return {"ok": False, "error": "deposit_usd must be between 100 and 1e9"}
    if interval_sec < 1 or interval_sec > 3600:
        return {"ok": False, "error": "interval_sec must be between 1 and 3600"}

    leverage = max(1.0, min(10.0, float(leverage)))

    symbols = [s.upper() for s in (cfg.risk.allowed_symbols or ["SPY", "QQQ"])]
    from aibroker.data.historical import load_history
    log.info("Loading historical data for %d symbols...", len(symbols))
    history = load_history(symbols, bars=750)
    log.info("Historical data loaded. Starting backtest walker.")

    start_bar = WARMUP_BARS
    if start_date:
        ref_sym = next(iter(history), None)
        if ref_sym:
            ref_bars = history[ref_sym]
            found = False
            for i, b in enumerate(ref_bars):
                bar_date = b.get("date", "")
                if i >= WARMUP_BARS and bar_date >= start_date:
                    start_bar = i
                    found = True
                    break
            if not found and len(ref_bars) > WARMUP_BARS:
                start_bar = max(WARMUP_BARS, len(ref_bars) - 5)
            actual_date = ref_bars[min(start_bar, len(ref_bars) - 1)].get("date", "?")
            print(f"[paper_start] start_date={start_date} => start_bar={start_bar} (actual date: {actual_date})")
        else:
            print(f"[paper_start] start_date={start_date} but no history loaded")
    else:
        print(f"[paper_start] no start_date, using default bar {start_bar}")

    _portfolio_mgr = None

    is_live = float(interval_sec) >= 3600
    with _lock:
        _session = PaperSession(
            running=True,
            initial_deposit_usd=float(deposit_usd),
            cash_usd=float(deposit_usd),
            interval_sec=float(interval_sec),
            started_at_utc=_utc_now_iso(),
            last_tick_at_utc=None,
            profile_name=cfg.profile_name,
            _next_step_mono=0.0,
            bar_index=start_bar,
            initial_bar_index=start_bar,
            history=history,
            leverage=leverage,
            live_mode=is_live,
        )
    return {"ok": True, **paper_status(cfg)}


def paper_stop() -> dict[str, Any]:
    global _session
    with _lock:
        if _session is not None:
            _session.running = False
    return {"ok": True, "running": False}


def paper_status(cfg: AppConfig) -> dict[str, Any]:
    with _lock:
        s = _session
        if s is None:
            return {
                "ok": True, "active": False, "running": False, "trades": [],
                "disclaimer": "דמו נייר בלבד — אין ברוקר, אין כסף אמיתי.",
            }
        mark = _mark_prices(s)
        equity = _equity(s, mark)
        pnl = equity - s.initial_deposit_usd

        mgr = _get_manager()
        positions_out = []
        for sym, p in s.positions.items():
            q = float(p.get("qty", 0.0))
            if abs(q) < 1e-9:
                continue
            meta = mgr.get_meta(sym)
            entry_px = float(p.get("avg_px", 0.0))
            cur_px = mark.get(sym.upper(), entry_px)
            if q > 0:
                pos_pnl_pct = ((cur_px - entry_px) / entry_px * 100) if entry_px > 0 else 0
            else:
                pos_pnl_pct = ((entry_px - cur_px) / entry_px * 100) if entry_px > 0 else 0
            positions_out.append({
                "symbol": sym,
                "qty": round(q, 4),
                "avg_px_usd": round(entry_px, 4),
                "side": "LONG" if q > 0 else "SHORT",
                "market_value_usd": round(abs(q) * cur_px, 2),
                "stop": round(meta.get("stop", 0), 2) if meta.get("stop") else None,
                "target": round(meta.get("target", 0), 2) if meta.get("target") else None,
                "pnl_pct": round(pos_pnl_pct, 2),
                "strategy": meta.get("strategy", ""),
                "symbol_leverage": round(meta.get("symbol_leverage", s.leverage), 1),
                "sentiment": round(_latest_sentiment.get(sym.upper(), 0.0), 2),
            })

        closed_summary = []
        for ct in s.closed_trades[-20:]:
            closed_summary.append({
                "symbol": ct.symbol, "side": ct.side, "pnl": ct.pnl,
                "r": ct.r_multiple, "strategy": ct.strategy,
            })

        max_bar = min(len(b) for b in s.history.values()) if s.history else s.bar_index
        oos_split_bar = s.initial_bar_index + int((max_bar - s.initial_bar_index) * 0.75)
        is_trades = [t for t in s.closed_trades if t.exit_bar < oos_split_bar]
        oos_trades = [t for t in s.closed_trades if t.exit_bar >= oos_split_bar]
        def _split_stats(trades):
            if not trades:
                return {"count": 0, "pnl": 0.0, "win_rate": 0.0, "avg_r": 0.0}
            pnl = sum(t.pnl for t in trades)
            wins = sum(1 for t in trades if t.pnl > 0)
            avg_r = sum(t.r_multiple for t in trades) / len(trades)
            return {
                "count": len(trades),
                "pnl": round(pnl, 2),
                "win_rate": round(wins / len(trades) * 100, 1),
                "avg_r": round(avg_r, 2),
            }

        return {
            "ok": True,
            "active": True,
            "running": s.running,
            "profile_name": cfg.profile_name,
            "started_at_utc": s.started_at_utc,
            "last_tick_at_utc": s.last_tick_at_utc,
            "interval_sec": s.interval_sec,
            "initial_deposit_usd": round(s.initial_deposit_usd, 2),
            "cash_usd": round(s.cash_usd, 2),
            "equity_usd": round(equity, 2),
            "pnl_usd": round(pnl, 2),
            "filled_trades": s.filled_trades,
            "tick_count": s.tick_count,
            "positions": positions_out,
            "trades": list(s.trades),
            "last_error": s.last_error,
            "sim_date": s.current_date,
            "days_simulated": s.days_simulated,
            "win_rate": s.win_rate,
            "avg_r": s.avg_r,
            "avg_win": s.avg_win,
            "avg_loss": s.avg_loss,
            "total_closed": len(s.closed_trades),
            "recent_closed": closed_summary,
            "leverage": s.leverage,
            "pnl_pct": round(pnl / s.initial_deposit_usd * 100, 2) if s.initial_deposit_usd > 0 else 0.0,
            "sentiment_available": bool(_latest_sentiment),
            "grok_key_set": bool(os.environ.get("GROK_API_KEY", "").strip()),
            "in_sample": _split_stats(is_trades),
            "out_of_sample": _split_stats(oos_trades),
            "disclaimer": "סימולציית סווינג על נתונים היסטוריים — כל בר = יום מסחר.",
        }


def _refresh_sentiment(symbols: list[str]) -> None:
    """Fetch news sentiment — called periodically from worker loop."""
    global _latest_sentiment
    try:
        from aibroker.news.ingest import fetch_sentiment_for_symbols
        _latest_sentiment = fetch_sentiment_for_symbols(symbols)
        log.debug("Sentiment updated for %d symbols", len(_latest_sentiment))
    except Exception as e:
        log.warning("Sentiment refresh failed: %s", e)


_last_sentiment_refresh: float = 0.0
_SENTIMENT_REFRESH_INTERVAL = 3600.0


def _paper_worker_loop() -> None:
    global _last_sentiment_refresh
    while not _worker_stop.is_set():
        if _worker_stop.wait(timeout=1.0):
            break
        try:
            if _profile_path is None:
                continue
            cfg = load_profile(_profile_path)

            s = _session
            if s is not None and s.running:
                now = time.monotonic()
                if now - _last_sentiment_refresh > _SENTIMENT_REFRESH_INTERVAL:
                    symbols = list(s.history.keys())
                    _refresh_sentiment(symbols)
                    _last_sentiment_refresh = now

            paper_tick(cfg)
        except Exception:
            log.exception("paper worker tick")


def start_paper_worker_thread() -> None:
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    _worker_stop.clear()
    _worker_thread = threading.Thread(target=_paper_worker_loop, name="paper-autopilot", daemon=True)
    _worker_thread.start()


def stop_paper_worker_thread() -> None:
    global _worker_thread
    _worker_stop.set()
    if _worker_thread is not None:
        _worker_thread.join(timeout=2.0)
        _worker_thread = None


def paper_fast_forward(cfg: AppConfig) -> dict[str, Any]:
    """Run all remaining bars instantly without delays."""
    global _session
    with _lock:
        s = _session
        if s is None:
            return {"ok": False, "error": "No active session"}
        if not s.running:
            return {"ok": True, **paper_status(cfg)}

        symbols = list(s.history.keys())
        _refresh_sentiment(symbols)

        max_steps = 2000
        steps = 0
        while s.running and steps < max_steps:
            try:
                _step_once(cfg, s)
                steps += 1
            except Exception as e:
                log.exception("fast-forward step failed")
                s.last_error = str(e)
                break
        log.info("Fast-forward complete: %d steps, equity $%.2f", steps, s.cash_usd)
    return {"ok": True, "steps_run": steps, **paper_status(cfg)}


def paper_step_once_for_test(cfg: AppConfig) -> None:
    with _lock:
        s = _session
        if s is None or not s.running:
            return
        _step_once(cfg, s)
