"""Agent session with simulation and live/paper modes."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from aibroker.agent.brain import AgentDecision, think, prepare_candidates, assess_market_regime
from aibroker.agent.collector import build_snapshot, collect_news, enrich_with_sentiment
from aibroker.agent.risk_profiles import RISK_PROFILES
from aibroker.data.historical import Bar, load_history

log = logging.getLogger(__name__)

_NY_TZ = ZoneInfo("America/New_York")


def _agent_trade_timestamp() -> str:
    """US market wall time for live/paper trade logs (consistent with US session)."""
    return datetime.now(_NY_TZ).strftime("%Y-%m-%d %H:%M ET")


Mode = Literal["sim", "paper", "live"]
RiskLevel = Literal["low", "medium", "high"]

DEFAULT_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "AMD"]


def _align_history_by_date(history: dict[str, list[Bar]]) -> dict[str, list[Bar]]:
    """Align all symbols to a common date index to prevent cross-sectional mismatches."""
    if not history:
        return history
    date_sets = [
        {str(b.get("date", "")) for b in bars if b.get("date")}
        for bars in history.values()
    ]
    if not date_sets:
        return history
    common_dates = sorted(set.intersection(*date_sets))
    if len(common_dates) < 30:
        return history

    aligned: dict[str, list[Bar]] = {}
    for sym, bars in history.items():
        by_date = {str(b.get("date", "")): b for b in bars}
        aligned[sym] = [by_date[d] for d in common_dates if d in by_date]
    return aligned


class AgentSession:
    def __init__(
        self,
        *,
        mode: Mode = "sim",
        symbols: list[str] | None = None,
        deposit: float = 100_000.0,
        start_date: str | None = None,
        risk_level: RiskLevel = "medium",
        profile_path: str | None = None,
    ):
        self.mode = mode
        self.symbols = [s.upper() for s in (symbols or DEFAULT_SYMBOLS)]
        self.initial_deposit = deposit
        self.cash = deposit
        self.positions: dict[str, dict[str, float]] = {}
        self.trades: deque[dict[str, Any]] = deque(maxlen=1000)
        self.decisions: deque[dict[str, Any]] = deque(maxlen=1000)
        self.step = 0
        self.running = False
        self.error: str | None = None
        self.risk_level: RiskLevel = risk_level if risk_level in RISK_PROFILES else "medium"
        self._profile_path: str | None = profile_path

        self._history: dict[str, list[Bar]] = {}
        self._bar_index = 0
        self._start_date = start_date
        self._news_cache: list[dict[str, str]] = []
        self._last_news_fetch = 0.0
        self._equity_peak: float = deposit
        self._equity_trough: float = deposit
        self._trailing_stops: dict[str, float] = {}
        self._last_rebalance: int = -999
        self._db_session_id: int = 0
        self._broker: Any = None
        self._news_lock = threading.Lock()
        self._news_fetch_in_progress = False
        self._indicators: dict | None = None
        self._pending_order_ids: list[str] = []

    def _price_for(self, symbol: str) -> float:
        bars = self._history.get(symbol, [])
        idx = min(self._bar_index, len(bars) - 1) if bars else -1
        if idx >= 0:
            return float(bars[idx]["c"])
        return 0.0

    def equity(self) -> float:
        eq = self.cash
        for sym, pos in self.positions.items():
            qty = pos.get("qty", 0)
            if qty == 0:
                continue
            px = self._price_for(sym) or pos.get("avg_cost", 0)
            eq += qty * px
        return eq

    def _margin_rate(self) -> float:
        rp = RISK_PROFILES.get(self.risk_level, RISK_PROFILES["medium"])
        return float(rp.get("margin_rate", 0.50))

    def _sim_total_margin(self) -> float:
        """Margin for all positions based on risk-level leverage."""
        rate = self._margin_rate()
        m = 0.0
        for sym, pos in self.positions.items():
            q = pos.get("qty", 0)
            if q == 0:
                continue
            px = self._price_for(sym) or float(pos.get("avg_cost", 0))
            m += abs(q) * px * rate
        return m

    def _sim_buying_power(self) -> float:
        """Equity-based buying power: equity minus total margin held for all positions."""
        return max(0.0, self.equity() - self._sim_total_margin())

    def _ensure_broker_connected(self) -> Any:
        """Reuse one broker client for paper/live ticks (loaded via factory when available)."""
        if self.mode not in ("paper", "live"):
            return None
        if self._broker is None:
            try:
                from aibroker.brokers.factory import make_broker
                from aibroker.config.loader import load_profile

                if not self._profile_path:
                    raise ValueError("no profile_path configured for broker connection")
                cfg = load_profile(self._profile_path)
                self._broker = make_broker(cfg)
            except Exception:
                from aibroker.brokers.alpaca import AlpacaBrokerClient

                self._broker = AlpacaBrokerClient(paper=(self.mode == "paper"))
            self._broker.connect()
        return self._broker

    def _request_news_refresh_async(self) -> None:
        """Refresh RSS/news in a daemon thread so the tick loop is not blocked."""
        if self.mode not in ("paper", "live"):
            return
        now = time.time()
        if now - self._last_news_fetch <= 300:
            return
        with self._news_lock:
            if self._news_fetch_in_progress:
                return
            self._news_fetch_in_progress = True

        symbols = list(self.symbols)

        def worker() -> None:
            try:
                cache = collect_news(symbols)
                with self._news_lock:
                    self._news_cache = cache
                    self._last_news_fetch = time.time()
            except Exception as e:
                log.warning("Background news fetch failed: %s", e)
            finally:
                with self._news_lock:
                    self._news_fetch_in_progress = False

        threading.Thread(target=worker, daemon=True).start()

    def start(self) -> dict[str, Any]:
        log.info("Agent session starting: mode=%s symbols=%s deposit=%s", self.mode, self.symbols, self.initial_deposit)
        self.running = True
        self.error = None
        self.step = 0
        self.cash = self.initial_deposit
        self.positions = {}
        self.trades = deque(maxlen=1000)
        self.decisions = deque(maxlen=1000)
        try:
            from aibroker.data.storage import save_session_start
            self._db_session_id = save_session_start(self.mode, self.risk_level, self.initial_deposit, self.symbols)
        except Exception as e:
            log.warning("Failed to save session start to DB: %s", e)
        self._equity_peak = self.initial_deposit
        self._equity_trough = self.initial_deposit
        self._trailing_stops = {}
        self._last_rebalance = -999

        if self.mode == "sim":
            self._history = load_history(self.symbols, bars=700)
            if not self._history:
                self.error = "no history data"
                self.running = False
                return self.status()
            self._history = _align_history_by_date(self._history)
            from aibroker.agent.fast_strategy import precompute_all
            self._indicators = precompute_all(self._history)
            if self._start_date:
                self._bar_index = self._find_start_index(self._start_date)
            else:
                first_len = len(list(self._history.values())[0])
                self._bar_index = max(50, first_len - 100)
        else:
            self._history = load_history(self.symbols, bars=200)
            self._bar_index = max(0, len(list(self._history.values())[0]) - 1) if self._history else 0
            try:
                self._ensure_broker_connected()
            except Exception as e:
                log.error("Alpaca connect on start failed: %s", e)
                self.error = str(e)
                self.running = False

        return self.status()

    def _find_start_index(self, date_str: str) -> int:
        first_sym_bars = list(self._history.values())[0] if self._history else []
        for i, bar in enumerate(first_sym_bars):
            if bar.get("date", "") >= date_str:
                return max(i, 20)
        return max(50, len(first_sym_bars) - 100)

    def _save_state(self) -> None:
        if self.mode in ("paper", "live"):
            try:
                from aibroker.agent.persistence import save_state
                save_state(self)
            except Exception as e:
                log.warning("Failed to save state: %s", e)

    def tick(self) -> dict[str, Any]:
        if not self.running:
            return self.status()

        if self.mode == "sim":
            return self._tick_sim()
        elif self.mode in ("paper", "live"):
            result = self._tick_live()
            self._save_state()
            return result
        return self.status()

    def tick_fast(self) -> dict[str, Any]:
        """Momentum rotation with configurable risk profile."""
        if not self.running:
            return self.status()
        raw_len = min(len(b) for b in self._history.values()) if self._history else 0
        max_len = raw_len - 1  # stop one bar early: last bar has no next-open for execution
        if max_len < 1 or self._bar_index >= max_len:
            self.running = False
            self._persist_to_db()
            return self.status()
        ref_bars = self._history.get("SPY") or (list(self._history.values())[0] if self._history else [])
        idx = self._bar_index
        if not ref_bars or idx >= len(ref_bars):
            self._bar_index += 1
            self.step += 1
            return self.status()
        sim_date = ref_bars[idx].get("date", str(idx))

        rp = RISK_PROFILES[self.risk_level]
        TARGET_POS = rp["target_positions"]
        REBAL_EVERY = rp["rebalance_every"]
        TRAIL_PCT = rp["trail_pct"]
        TRAIL_ATR = rp["trail_atr_mult"]
        INVEST_PCT = rp["invest_pct"]
        ALLOW_SHORTS = rp["allow_shorts"]
        ROT_THRESH = rp["rotation_threshold"]

        from aibroker.agent.fast_strategy import rank_symbols, detect_bear_regime, precompute_all

        if self._indicators is None:
            self._indicators = precompute_all(self._history)

        spy_ind = self._indicators.get("SPY") or (next(iter(self._indicators.values())) if self._indicators else None)
        bear, spy_rsi = detect_bear_regime(spy_ind, idx, rp["bear_trigger"])

        w10 = float(rp.get("momentum_w10", 0.25))
        w20 = float(rp.get("momentum_w20", 0.40))
        w50 = float(rp.get("momentum_w50", 0.35))
        rankings = rank_symbols(self._indicators, idx, self.symbols, (w10, w20, w50))

        if not rankings:
            self._bar_index += 1
            self.step += 1
            return self.status()

        actions: list[tuple[str, str, int, str]] = []
        eq = self.equity()
        if eq <= 0:
            eq = 1.0
        do_rebalance = (self.step - self._last_rebalance) >= REBAL_EVERY

        for sym, p in list(self.positions.items()):
            q = p.get("qty", 0)
            if q == 0:
                continue
            avg = p.get("avg_cost", 0)
            r = next((x for x in rankings if x["symbol"] == sym), None)
            if not r:
                continue
            price = r["price"]
            bar_hi = float(r.get("bar_high", price))
            bar_lo = float(r.get("bar_low", price))
            atr_val = r["atr"] or 1

            if q > 0:
                trail_base = max(avg * TRAIL_PCT, avg - TRAIL_ATR * atr_val)
                trail = self._trailing_stops.get(sym, trail_base)
                new_trail = max(trail, bar_hi - TRAIL_ATR * atr_val, bar_hi * TRAIL_PCT)
                self._trailing_stops[sym] = new_trail

                if bar_lo <= new_trail:
                    pnl_pct = (price / avg - 1) * 100 if avg > 0 else 0
                    actions.append((sym, "sell", abs(int(q)),
                                    f"סטופ נגרר ${new_trail:.0f} | {pnl_pct:+.1f}%"))
                    self._trailing_stops.pop(sym, None)

            elif q < 0:
                stop_up = 2.0 - TRAIL_PCT
                trail_base = min(avg * (1 + stop_up), avg + TRAIL_ATR * atr_val)
                trail = self._trailing_stops.get(sym, trail_base)
                new_trail = min(trail, bar_lo + TRAIL_ATR * atr_val, bar_lo * (1 + stop_up))
                self._trailing_stops[sym] = new_trail

                if bar_hi >= new_trail:
                    pnl_pct = (avg / price - 1) * 100 if price > 0 else 0
                    actions.append((sym, "cover", abs(int(q)),
                                    f"סטופ נגרר ${new_trail:.0f} | {pnl_pct:+.1f}%"))
                    self._trailing_stops.pop(sym, None)

        if do_rebalance:
            self._last_rebalance = self.step
            closed_syms = {a[0] for a in actions}

            if bear and rp["bear_sell_all"]:
                for sym, p in list(self.positions.items()):
                    q = p.get("qty", 0)
                    if q > 0 and sym not in closed_syms:
                        r = next((x for x in rankings if x["symbol"] == sym), None)
                        pnl_pct = 0
                        if r:
                            pnl_pct = (r["price"] / p.get("avg_cost", r["price"]) - 1) * 100
                        actions.append((sym, "sell", abs(int(q)),
                                        f"שוק דובי | {pnl_pct:+.1f}%"))
                        self._trailing_stops.pop(sym, None)
                        closed_syms.add(sym)

                if ALLOW_SHORTS:
                    weak = sorted([r for r in rankings if r["momentum"] < -8
                                   and not r["above_ma50"] and r["symbol"] not in closed_syms
                                   and r["symbol"] not in self.positions],
                                  key=lambda x: x["momentum"])
                    for r in weak[:2]:
                        if r["price"] <= 0:
                            continue
                        alloc = eq * 0.12
                        qty = max(1, int(alloc / r["price"]))
                        m_rate = self._margin_rate()
                        margin = qty * r["price"] * m_rate
                        bp = self._sim_buying_power()
                        if margin > bp * 0.25:
                            qty = max(1, int(bp * 0.25 / (r["price"] * m_rate)))
                        if qty > 0:
                            actions.append((r["symbol"], "short", qty,
                                            f"שוק דובי | מומנטום {r['momentum']:+.1f}%"))
                            self._trailing_stops[r["symbol"]] = r["price"] * (1 + (2.0 - TRAIL_PCT))

            else:
                for sym, p in list(self.positions.items()):
                    q = p.get("qty", 0)
                    if q < 0 and sym not in closed_syms:
                        actions.append((sym, "cover", abs(int(q)), "שוק עולה — סוגר שורטים"))
                        self._trailing_stops.pop(sym, None)
                        closed_syms.add(sym)

                ranked = sorted(rankings, key=lambda x: -x["momentum"])
                candidates = [r for r in ranked if r["above_ma50"] and r["momentum"] > 0]
                if not candidates:
                    candidates = [r for r in ranked if r["momentum"] > 0][:TARGET_POS]
                top = candidates[:TARGET_POS]
                top_syms = {r["symbol"] for r in top}

                for sym, p in list(self.positions.items()):
                    q = p.get("qty", 0)
                    if q > 0 and sym not in top_syms and sym not in closed_syms:
                        r = next((x for x in rankings if x["symbol"] == sym), None)
                        if r and r["momentum"] < ROT_THRESH:
                            pnl_pct = (r["price"] / p.get("avg_cost", r["price"]) - 1) * 100
                            actions.append((sym, "sell", abs(int(q)),
                                            f"רוטציה — מומנטום {r['momentum']:+.1f}% | {pnl_pct:+.1f}%"))
                            self._trailing_stops.pop(sym, None)
                            closed_syms.add(sym)

                available = self._sim_buying_power()
                for a_sym, a_act, a_qty, _ in actions:
                    r = next((x for x in rankings if x["symbol"] == a_sym), None)
                    if r:
                        if a_act in ("sell", "cover"):
                            available += a_qty * r["price"]

                alloc_per = eq * INVEST_PCT / max(TARGET_POS, 1)
                trim_hi = float(rp.get("rebalance_trim_above", 1.35))
                for r in top:
                    sym = r["symbol"]
                    if sym in closed_syms:
                        continue
                    if r["price"] <= 0:
                        continue
                    existing_qty = self.positions.get(sym, {}).get("qty", 0)
                    if existing_qty > 0:
                        existing_val = existing_qty * r["price"]
                        if existing_val < alloc_per * 0.7:
                            add_qty = max(1, int((alloc_per - existing_val) / r["price"]))
                            cost = add_qty * r["price"]
                            if cost <= available * 0.9:
                                available -= cost
                                actions.append((sym, "buy", add_qty,
                                                f"הוספה | מומנטום {r['momentum']:+.1f}%"))
                        elif existing_val > alloc_per * trim_hi:
                            target_qty = max(1, int(alloc_per / r["price"]))
                            trim_qty = int(existing_qty - target_qty)
                            if trim_qty > 0:
                                w_pct = (existing_val / eq * 100) if eq > 0 else 0
                                actions.append((
                                    sym, "sell", trim_qty,
                                    f"איזון — מעל יעד (~{w_pct:.0f}% מההון)",
                                ))
                                available += trim_qty * r["price"]
                                self._trailing_stops.pop(sym, None)
                                closed_syms.add(sym)
                        continue
                    qty = max(1, int(alloc_per / r["price"]))
                    cost = qty * r["price"]
                    if cost > available * 0.95:
                        qty = max(1, int(available * 0.95 / r["price"]))
                    if qty <= 0:
                        continue
                    available -= qty * r["price"]
                    actions.append((sym, "buy", qty,
                                    f"מומנטום {r['momentum']:+.1f}% | ROC20 {r['roc20']:+.1f}% | RSI {r['rsi']:.0f}"))
                    self._trailing_stops[sym] = max(r["price"] * TRAIL_PCT, r["price"] - TRAIL_ATR * r["atr"])

        executed_actions: list[tuple[str, str, int, str]] = []
        for sym, action, qty, reason in actions:
            if not self._sim_risk_allows(sym, action, qty):
                continue
            self._execute_sim(sym, action, qty, reason, sim_date)
            executed_actions.append((sym, action, qty, reason))
        actions = executed_actions

        regime_label = "דובי" if bear else "שורי"
        open_count = sum(1 for p in self.positions.values() if p.get("qty", 0) != 0)
        invested_pct = (1 - self.cash / eq) * 100 if eq > 0 else 0
        self.decisions.append({
            "step": self.step,
            "date": sim_date,
            "actions": [{"symbol": s, "action": a, "quantity": q, "reason": r} for s, a, q, r in actions],
            "market_view": f"משטר: {regime_label} | SPY RSI {spy_rsi:.0f} | סיכון: {rp['label_he']}" if spy_rsi else f"משטר: {regime_label} | סיכון: {rp['label_he']}",
            "risk_note": f"מושקע: {invested_pct:.0f}% | פוזיציות: {open_count}",
        })
        self._bar_index += 1
        self.step += 1
        return self.status()

    def _tick_sim(self) -> dict[str, Any]:
        raw_len = min(len(b) for b in self._history.values()) if self._history else 0
        max_len = raw_len - 1
        ref_bars = self._history.get("SPY", list(self._history.values())[0] if self._history else [])
        if max_len < 1 or self._bar_index >= max_len:
            self.running = False
            self._persist_to_db()
            return self.status()

        sim_date = ref_bars[self._bar_index].get("date", str(self._bar_index)) if self._bar_index < len(ref_bars) else str(self._bar_index)

        # Tier 1: Fetch live news for sim (cached, almost free)
        sim_news = self._get_sim_news()

        # Tier 1: Macro regime assessment (once per day, cached)
        regime = "neutral"
        if sim_news:
            try:
                regime = assess_market_regime(sim_news, current_date=sim_date)
            except Exception as e:
                log.warning("Macro regime failed: %s", e)

        # Tier 1: Algorithmic candidate screening
        from aibroker.agent.fast_strategy import precompute_all
        if self._indicators is None:
            self._indicators = precompute_all(self._history)

        sentiment_scores: dict = {}
        if sim_news:
            try:
                sentiment_scores = enrich_with_sentiment(self.symbols, sim_news)
            except Exception as e:
                log.warning("Sentiment enrichment failed: %s", e)

        candidates = prepare_candidates(
            self._indicators,
            self._bar_index,
            self.symbols,
            self.risk_level,
            sentiment_scores=sentiment_scores,
        )

        snapshot = build_snapshot(
            symbols=self.symbols,
            history=self._history,
            bar_index=self._bar_index,
            positions=self.positions,
            cash=self.cash,
            initial_deposit=self.initial_deposit,
            news=sim_news,
            sim_date=sim_date,
        )
        snapshot["risk_level"] = self.risk_level
        snapshot["regime"] = regime
        snapshot["candidates"] = candidates
        rp = RISK_PROFILES.get(self.risk_level, RISK_PROFILES["medium"])
        snapshot["portfolio"]["buying_power"] = round(self._sim_buying_power(), 2)
        snapshot["portfolio"]["leverage"] = rp.get("leverage", 2.0)

        try:
            decision = think(snapshot, allowed_symbols=self.symbols)
        except Exception as e:
            log.error("Agent think error: %s", e)
            self.error = str(e)
            self._bar_index += 1
            self.step += 1
            return self.status()

        avoid_set = {s.upper() for s in decision.avoid_symbols}
        priority_set = {s.upper() for s in decision.priority_symbols}
        agg = decision.aggression
        agg_mult = {"conservative": 0.6, "normal": 1.0, "aggressive": 1.4}.get(agg, 1.0)
        cb = decision.cash_bias

        eq = self.equity()
        cash_floor = eq * decision.cash_target_pct / 100.0 if eq > 0 else 0

        for act in decision.actions:
            sym_upper = act.symbol.upper()
            if sym_upper in avoid_set:
                log.info("Skipping %s %s — in avoid_symbols", act.action, act.symbol)
                continue
            eb = decision.exposure_bias
            if act.action in ("buy", "short") and eb == "mostly_cash":
                log.info("Skipping %s %s — exposure_bias is mostly_cash", act.action, act.symbol)
                continue
            if act.action == "short" and eb == "net_long":
                log.info("Skipping short %s — exposure_bias is net_long", act.symbol)
                continue
            if act.action == "buy" and eb == "net_short":
                log.info("Skipping buy %s — exposure_bias is net_short", act.symbol)
                continue
            qty = act.quantity

            if act.action in ("buy", "short"):
                qty = int(qty * agg_mult) if agg_mult != 1.0 else qty
                if cb == "raise":
                    qty = int(qty * 0.7)
                elif cb == "deploy":
                    qty = int(qty * 1.2)
                if sym_upper in priority_set:
                    qty = int(qty * 1.25)
                qty = max(1, qty) if act.quantity > 0 else 0
            elif act.action in ("sell", "cover") and cb == "raise":
                qty = max(qty, int(qty * 1.2))

            est_px = self._next_bar_open(act.symbol) or self._price_for(act.symbol)
            if act.action == "buy" and self.cash - (qty * est_px) < cash_floor:
                affordable = max(0, int((self.cash - cash_floor) / max(est_px, 1)))
                if affordable <= 0:
                    log.info("Skipping buy %s — would breach cash_target_pct %.0f%%", act.symbol, decision.cash_target_pct)
                    continue
                qty = affordable
            if not self._sim_risk_allows(act.symbol, act.action, qty):
                continue
            self._execute_sim(act.symbol, act.action, qty, act.reason, sim_date)

        self.decisions.append({
            "step": self.step,
            "date": sim_date,
            **decision.to_dict(),
        })

        self._bar_index += 1
        self.step += 1
        return self.status()

    def _get_sim_news(self) -> list[dict[str, str]]:
        """Fetch live RSS headlines for sim mode (cached for 5 min)."""
        now = time.time()
        if now - self._last_news_fetch <= 300 and self._news_cache:
            return list(self._news_cache)
        try:
            news = collect_news(self.symbols)
            self._news_cache = news
            self._last_news_fetch = now
            return news
        except Exception as e:
            log.warning("Sim news fetch failed: %s", e)
            return list(self._news_cache)

    def _sim_risk_allows(self, symbol: str, action: str, qty: int) -> bool:
        """Enforce risk limits in simulation: drawdown cap, per-symbol exposure.

        Reads thresholds from RISK_PROFILES (single source of truth).
        """
        eq = self.equity()
        rp = RISK_PROFILES.get(self.risk_level, RISK_PROFILES["medium"])

        max_dd_pct = float(rp.get("max_drawdown_pct", 0.40))
        if eq < self.initial_deposit * (1 - max_dd_pct):
            log.debug("Risk gate blocked %s %s: drawdown > %d%%", action, symbol, int(max_dd_pct * 100))
            return False

        max_sym_pct = float(rp.get("max_symbol_exposure_pct", 0.35))
        if action in ("buy", "short") and eq > 0:
            price = self._next_bar_open(symbol)
            if price > 0:
                new_notional = qty * price
                existing = abs(float(self.positions.get(symbol, {}).get("qty", 0))) * price
                if (existing + new_notional) / eq > max_sym_pct:
                    log.debug("Risk gate blocked %s %s: exposure > %d%%", action, symbol, int(max_sym_pct * 100))
                    return False
        return True

    def _next_bar_open(self, symbol: str) -> float:
        """Return next bar's open price for lookahead-free execution."""
        bars = self._history.get(symbol, [])
        next_idx = self._bar_index + 1
        if not bars or next_idx >= len(bars):
            if bars and self._bar_index < len(bars):
                return float(bars[self._bar_index]["c"])
            return 0.0
        return float(bars[next_idx].get("o", bars[next_idx]["c"]))

    def _execute_sim(self, symbol: str, action: str, qty: int, reason: str, date: str) -> None:
        """Unified signed-position execution using NEXT bar open to avoid lookahead bias."""
        if action == "hold" or qty <= 0:
            return
        price = self._next_bar_open(symbol)
        if price <= 0:
            return

        pos = self.positions.get(symbol, {"qty": 0, "avg_cost": 0, "opened": date})
        old_qty = float(pos["qty"])
        old_avg = float(pos["avg_cost"])
        old_opened = pos.get("opened", date)

        if action == "buy":
            bp = self._sim_buying_power()
            cost = price * qty
            if cost > bp:
                qty = int(bp / price)
                if qty <= 0:
                    return
            delta = qty
            self.cash -= price * qty

        elif action == "sell":
            if old_qty <= 0:
                return
            qty = min(qty, int(old_qty))
            if qty <= 0:
                return
            delta = -qty
            self.cash += price * qty

        elif action == "short":
            rate = self._margin_rate()
            margin = price * qty * rate
            bp = self._sim_buying_power()
            if margin > bp:
                qty = int(bp / (price * rate))
                if qty <= 0:
                    return
            delta = -qty
            self.cash += price * qty

        elif action == "cover":
            if old_qty >= 0:
                return
            qty = min(qty, abs(int(old_qty)))
            if qty <= 0:
                return
            delta = qty
            self.cash -= price * qty
        else:
            return

        new_qty = old_qty + delta

        if abs(new_qty) < 0.01:
            self.positions.pop(symbol, None)
        else:
            if (old_qty > 0 and delta > 0) or (old_qty < 0 and delta < 0):
                total_abs = abs(old_qty) + abs(delta)
                new_avg = (abs(old_qty) * old_avg + abs(delta) * price) / total_abs
                opened_eff = old_opened
            elif old_qty * new_qty > 0:
                new_avg = old_avg
                opened_eff = old_opened
            else:
                new_avg = price
                opened_eff = date

            self.positions[symbol] = {"qty": new_qty, "avg_cost": round(new_avg, 4), "opened": opened_eff}

        self.trades.append({
            "step": self.step, "date": date, "symbol": symbol,
            "action": action, "price": round(price, 2), "qty": abs(qty),
            "reason": reason,
        })

    def _apply_broker_positions(self, pos_list: list[dict[str, Any]], prev: dict[str, dict[str, Any]]) -> None:
        """Merge Alpaca positions into self.positions, preserving opened when direction unchanged."""
        self.positions = {}
        for p in pos_list:
            sym = str(p["symbol"])
            nq = float(p["qty"])
            pr = prev.get(sym, {})
            pq = float(pr.get("qty", 0))
            op = str(pr.get("opened", "") or "")
            if pq * nq > 0:
                opened = op
            elif nq == 0:
                opened = ""
            else:
                opened = ""
            self.positions[sym] = {
                "qty": nq,
                "avg_cost": float(p["avg_cost"]),
                "opened": opened,
            }

    def _live_cap_order_qty(
        self,
        acct: dict[str, Any],
        est_px: float,
        symbol: str,
        requested: int,
        side: str,
    ) -> int:
        """Limit LLM size vs buying power, per-symbol exposure, and margin.

        Reads thresholds from RISK_PROFILES (single source of truth).
        """
        if est_px <= 0 or requested <= 0:
            return 0
        bp = float(acct.get("buying_power_usd", 0) or 0)
        sym = symbol.upper()
        cur = self.positions.get(sym, {})
        cur_q = float(cur.get("qty", 0))

        rp = RISK_PROFILES.get(self.risk_level, RISK_PROFILES["medium"])
        max_sym_pct = float(rp.get("max_symbol_exposure_pct", 0.35))

        eq = float(acct.get("equity_usd", 0) or 0)
        if eq <= 0:
            eq = self.equity()

        if side == "buy":
            max_q = int(max(0, bp * 0.95 / est_px))
            capped = max(0, min(int(requested), max_q))
        elif cur_q > 0:
            capped = max(0, min(int(requested), int(cur_q)))
        else:
            margin_per = est_px * self._margin_rate()
            if margin_per <= 0:
                return 0
            max_q = int(max(0, bp * 0.9 / margin_per))
            capped = max(0, min(int(requested), max_q))

        # Exposure cap only applies when *opening* or *increasing* a position.
        # sell-to-close (cur_q > 0 and side == "sell") reduces exposure — skip cap.
        opening = (side == "buy" and cur_q >= 0) or (side == "sell" and cur_q <= 0)
        if eq > 0 and opening and capped > 0:
            existing_notional = abs(cur_q) * est_px
            new_notional = capped * est_px
            if (existing_notional + new_notional) / eq > max_sym_pct:
                allowed_notional = max(0, eq * max_sym_pct - existing_notional)
                capped = min(capped, max(0, int(allowed_notional / est_px)))
                if capped <= 0:
                    log.info("Live risk: %s %s blocked — exposure > %d%%", side, sym, int(max_sym_pct * 100))
                    return 0

        return capped

    def _live_resync_from_broker(
        self,
        broker: Any,
        *,
        qty_baseline: dict[str, float] | None,
    ) -> None:
        """Refresh cash/positions. If qty_baseline is set (pre-order snapshot), poll until it differs or cap."""
        import time

        prev = dict(self.positions)
        attempts = 10 if qty_baseline is not None else 1
        baseline = qty_baseline or {}
        for i in range(attempts):
            if i > 0:
                time.sleep(0.25)
            acct = broker.get_account() or {}
            if acct:
                self.cash = acct.get("cash_usd", self.cash)
            pos_list = broker.positions()
            cur = {p["symbol"]: float(p["qty"]) for p in pos_list}
            changed = any(abs(cur.get(k, 0) - baseline.get(k, 0)) > 1e-6 for k in set(cur) | set(baseline))
            self._apply_broker_positions(pos_list, prev)
            prev = dict(self.positions)
            if qty_baseline is None or changed or i == attempts - 1:
                break

    def _resolve_pending_orders(self, broker: Any) -> None:
        """Re-poll any pending orders from the previous tick before making new decisions."""
        if not self._pending_order_ids:
            return
        resolved: list[str] = []
        for oid in self._pending_order_ids:
            try:
                fill = broker.poll_order_fill(oid, timeout_s=3.0, interval_s=0.5)
                status = str(fill.get("status", ""))
                if status in ("filled", "partially_filled", "canceled", "expired", "rejected"):
                    resolved.append(oid)
                    log.info("Pending order %s resolved: %s", oid, status)
                else:
                    log.info("Pending order %s still open: %s", oid, status)
            except Exception as e:
                log.warning("Failed to poll pending order %s: %s", oid, e)
        for oid in resolved:
            self._pending_order_ids.remove(oid)

    def _tick_live(self) -> dict[str, Any]:
        self._request_news_refresh_async()

        self._history = load_history(self.symbols, bars=200)
        if self._history:
            self._bar_index = len(list(self._history.values())[0]) - 1

        with self._news_lock:
            safe_news = list(self._news_cache)

        # Tier 1: Macro regime assessment
        regime = "neutral"
        if safe_news:
            try:
                from datetime import date as _date_cls
                regime = assess_market_regime(safe_news, current_date=_date_cls.today().isoformat())
            except Exception as e:
                log.warning("Live macro regime failed: %s", e)

        # Tier 1: Algorithmic candidate screening
        from aibroker.agent.fast_strategy import precompute_all
        if self._history:
            live_indicators = precompute_all(self._history)
            sentiment_scores: dict = {}
            if safe_news:
                try:
                    sentiment_scores = enrich_with_sentiment(self.symbols, safe_news)
                except Exception as e:
                    log.warning("Live sentiment failed: %s", e)
            candidates = prepare_candidates(
                live_indicators,
                self._bar_index,
                self.symbols,
                self.risk_level,
                sentiment_scores=sentiment_scores,
            )
        else:
            candidates = []

        snapshot = build_snapshot(
            symbols=self.symbols,
            history=self._history,
            bar_index=self._bar_index,
            positions=self.positions,
            cash=self.cash,
            initial_deposit=self.initial_deposit,
            news=safe_news,
        )
        snapshot["risk_level"] = self.risk_level
        snapshot["regime"] = regime
        snapshot["candidates"] = candidates

        try:
            broker = self._ensure_broker_connected()
        except Exception as e:
            log.error("Alpaca connect failed: %s", e)
            self.error = str(e)
            self.step += 1
            return self.status()

        self._resolve_pending_orders(broker)

        acct_live: dict[str, Any] = {}
        qty_baseline: dict[str, float] = {}
        try:
            acct_live = broker.get_account() or {}
            if acct_live:
                self.cash = acct_live.get("cash_usd", self.cash)
            pos_list = broker.positions()
            prev_state = dict(self.positions)
            qty_baseline = {p["symbol"]: float(p["qty"]) for p in pos_list}
            self._apply_broker_positions(pos_list, prev_state)
        except Exception as e:
            log.error("Pre-tick Alpaca sync failed, aborting tick: %s", e)
            self.error = f"Alpaca sync: {e}"
            self.step += 1
            return self.status()

        try:
            decision = think(snapshot, allowed_symbols=self.symbols)
        except Exception as e:
            log.error("Agent think error: %s", e)
            self.error = str(e)
            self.step += 1
            return self.status()

        from aibroker.brokers.base import OrderIntent

        avoid_set = {s.upper() for s in decision.avoid_symbols}
        priority_set = {s.upper() for s in decision.priority_symbols}
        agg = decision.aggression
        agg_mult = {"conservative": 0.6, "normal": 1.0, "aggressive": 1.4}.get(agg, 1.0)
        cb = decision.cash_bias

        live_eq = float(acct_live.get("equity_usd", 0) or 0)
        if live_eq <= 0:
            live_eq = self.equity()
        live_cash = float(acct_live.get("cash_usd", 0) or 0)
        cash_floor = live_eq * decision.cash_target_pct / 100.0 if live_eq > 0 else 0

        had_submitted_ok = False
        for act in decision.actions:
            sym_upper = act.symbol.upper()
            if sym_upper in avoid_set:
                log.info("Live: skipping %s %s — in avoid_symbols", act.action, act.symbol)
                continue
            eb = decision.exposure_bias
            if act.action in ("buy", "short") and eb == "mostly_cash":
                log.info("Live: skipping %s %s — exposure_bias is mostly_cash", act.action, act.symbol)
                continue
            if act.action == "short" and eb == "net_long":
                log.info("Live: skipping short %s — exposure_bias is net_long", act.symbol)
                continue
            if act.action == "buy" and eb == "net_short":
                log.info("Live: skipping buy %s — exposure_bias is net_short", act.symbol)
                continue
            side = act.action
            if side == "short":
                side = "sell"
            elif side == "cover":
                side = "buy"
            if side not in ("buy", "sell") or act.quantity <= 0:
                continue
            raw_qty = act.quantity
            if act.action in ("buy", "short"):
                raw_qty = int(raw_qty * agg_mult) if agg_mult != 1.0 else raw_qty
                if cb == "raise":
                    raw_qty = int(raw_qty * 0.7)
                elif cb == "deploy":
                    raw_qty = int(raw_qty * 1.2)
                if sym_upper in priority_set:
                    raw_qty = int(raw_qty * 1.25)
                raw_qty = max(1, raw_qty)
            elif act.action in ("sell", "cover") and cb == "raise":
                raw_qty = max(raw_qty, int(raw_qty * 1.2))
            est = broker.estimate_fill_price(act.symbol, side)
            if act.action == "buy" and est > 0 and live_cash - (raw_qty * est) < cash_floor:
                affordable = max(0, int((live_cash - cash_floor) / max(est, 1)))
                if affordable <= 0:
                    log.info("Live: skipping buy %s — would breach cash_target_pct %.0f%%",
                             act.symbol, decision.cash_target_pct)
                    continue
                raw_qty = affordable
            qty_cap = self._live_cap_order_qty(
                acct_live, est, act.symbol, int(raw_qty), side,
            )
            if qty_cap <= 0:
                log.warning("Order skipped after risk cap: %s %s", act.symbol, act.action)
                self.trades.append({
                    "step": self.step,
                    "date": _agent_trade_timestamp(),
                    "symbol": act.symbol,
                    "action": act.action,
                    "price": 0,
                    "qty": int(act.quantity),
                    "reason": act.reason,
                    "broker_ok": False,
                    "broker_msg": "כמות אפס אחרי תקרת כוח קנייה / מחיר משוער",
                })
                continue
            intent = OrderIntent(
                symbol=act.symbol,
                side=side,
                quantity=float(qty_cap),
            )
            result = broker.place_order(intent)
            log.info("Order result: %s", result)
            fill = {"status": "none", "filled_avg_price": 0.0, "filled_qty": 0}
            if result.ok:
                had_submitted_ok = True
                try:
                    acct_live = broker.get_account() or acct_live
                except Exception:
                    pass
                if result.broker_order_id:
                    fill = broker.poll_order_fill(result.broker_order_id)
                    fill_status = str(fill.get("status", ""))
                    if fill_status not in ("filled",) and result.broker_order_id:
                        self._pending_order_ids.append(result.broker_order_id)
            px = float(fill.get("filled_avg_price") or 0.0)
            fq = int(fill.get("filled_qty") or 0)
            self.trades.append({
                "step": self.step,
                "date": _agent_trade_timestamp(),
                "symbol": act.symbol,
                "action": act.action,
                "price": round(px, 4) if px > 0 else 0,
                "qty": fq if fq > 0 else qty_cap,
                "reason": act.reason,
                "broker_ok": result.ok,
                "broker_msg": result.message,
                "order_status": str(fill.get("status", "")),
                "fill_pending": bool(result.ok and px <= 0),
            })

        try:
            self._live_resync_from_broker(
                broker,
                qty_baseline=qty_baseline if had_submitted_ok else None,
            )
        except Exception as e:
            log.warning("Failed to sync account: %s", e)

        self.decisions.append({
            "step": self.step,
            "date": _agent_trade_timestamp(),
            **decision.to_dict(),
        })
        self.step += 1
        self.error = None
        return self.status()

    def stop(self) -> dict[str, Any]:
        self.running = False
        self._persist_to_db()
        if self._broker is not None:
            try:
                self._broker.disconnect()
            except Exception:
                pass
            self._broker = None
        try:
            from aibroker.agent.persistence import mark_stopped
            mark_stopped()
        except Exception:
            pass
        return self.status()

    def _persist_to_db(self) -> None:
        if not self._db_session_id:
            return
        try:
            from aibroker.data.storage import save_session_end, save_trades, save_decisions, _get_db, _sqlite_retry_write
            eq = self.equity()
            pnl = eq - self.initial_deposit
            pnl_pct = (pnl / self.initial_deposit * 100) if self.initial_deposit > 0 else 0
            save_session_end(
                self._db_session_id, eq, pnl, pnl_pct,
                self.step, len(self.trades),
                self._equity_peak - self.initial_deposit,
                self._equity_trough - self.initial_deposit,
            )
            sid = self._db_session_id

            def _clean_and_insert() -> None:
                db = _get_db()
                db.execute("DELETE FROM trades WHERE session_id=?", (sid,))
                db.execute("DELETE FROM decisions WHERE session_id=?", (sid,))
                db.commit()

            _sqlite_retry_write(_clean_and_insert)
            save_trades(sid, list(self.trades))
            save_decisions(sid, list(self.decisions)[-50:])
            log.info("Session %d persisted to DB", self._db_session_id)
        except Exception as e:
            log.warning("Failed to persist session to DB: %s", e)

    def status(self) -> dict[str, Any]:
        first_bars = list(self._history.values())[0] if self._history else []
        total_bars = min(len(b) for b in self._history.values()) if self._history else 0
        current_date = ""
        if first_bars and 0 <= self._bar_index < total_bars:
            current_date = first_bars[self._bar_index].get("date", "")
        elif first_bars:
            current_date = first_bars[-1].get("date", "")

        eq = self.equity()
        self._equity_peak = max(self._equity_peak, eq)
        self._equity_trough = min(self._equity_trough, eq)
        pnl = eq - self.initial_deposit
        pnl_pct = (pnl / self.initial_deposit * 100) if self.initial_deposit > 0 else 0
        pnl_peak = self._equity_peak - self.initial_deposit
        pnl_trough = self._equity_trough - self.initial_deposit

        positions_detail = []
        for sym, p in self.positions.items():
            qty = p.get("qty", 0)
            avg = p.get("avg_cost", 0)
            px = self._price_for(sym) or avg
            side = "long" if qty > 0 else "short"
            market_val = abs(qty) * px
            cost_val = abs(qty) * avg
            upl = (px - avg) * qty
            upl_pct = ((px / avg - 1) * 100 * (1 if qty > 0 else -1)) if avg > 0 else 0
            bar_data = {}
            bars = self._history.get(sym, [])
            idx = min(self._bar_index, len(bars) - 1) if bars else -1
            if idx >= 0:
                b = bars[idx]
                bar_data = {"o": float(b.get("o", px)), "h": float(b.get("h", px)),
                            "l": float(b.get("l", px)), "c": float(b.get("c", px))}
            positions_detail.append({
                "symbol": sym,
                "side": side,
                "qty": abs(qty),
                "avg_cost": round(avg, 2),
                "current_price": round(px, 2),
                "market_value": round(market_val, 2),
                "unrealized_pnl": round(upl, 2),
                "unrealized_pnl_pct": round(upl_pct, 2),
                "opened": p.get("opened", ""),
                **bar_data,
            })

        remaining = max(0, total_bars - self._bar_index)
        return {
            "running": self.running,
            "mode": self.mode,
            "risk_level": self.risk_level,
            "step": self.step,
            "total_bars": total_bars,
            "remaining": remaining,
            "date": current_date,
            "cash": round(self.cash, 2),
            "equity": round(eq, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "equity_peak": round(self._equity_peak, 2),
            "equity_trough": round(self._equity_trough, 2),
            "pnl_peak": round(pnl_peak, 2),
            "pnl_trough": round(pnl_trough, 2),
            "positions": {sym: {"qty": p["qty"], "avg_cost": round(p["avg_cost"], 2)} for sym, p in self.positions.items()},
            "positions_detail": positions_detail,
            "trade_count": len(self.trades),
            "last_decision": self.decisions[-1] if self.decisions else None,
            "last_trades": list(self.trades)[-10:],
            "error": self.error,
        }
