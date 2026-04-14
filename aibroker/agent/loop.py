"""Agent session with simulation and live/paper modes."""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Literal

from aibroker.agent.brain import AgentDecision, think
from aibroker.agent.collector import build_snapshot, collect_news
from aibroker.data.historical import Bar, load_history

log = logging.getLogger(__name__)

Mode = Literal["sim", "paper", "live"]
RiskLevel = Literal["low", "medium", "high"]

DEFAULT_SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "AMD"]

RISK_PROFILES: dict[str, dict[str, Any]] = {
    "low": {
        "target_positions": 6,
        "rebalance_every": 20,
        "trail_pct": 0.88,
        "trail_atr_mult": 4.0,
        "invest_pct": 0.80,
        "allow_shorts": False,
        "bear_sell_all": True,
        "bear_trigger": "below_200",
        "rotation_threshold": -5.0,
        "label_he": "נמוך",
        "momentum_w10": 0.25,
        "momentum_w20": 0.40,
        "momentum_w50": 0.35,
    },
    "medium": {
        "target_positions": 5,
        "rebalance_every": 10,
        "trail_pct": 0.82,
        "trail_atr_mult": 5.0,
        "invest_pct": 0.95,
        "allow_shorts": False,
        "bear_sell_all": True,
        "bear_trigger": "below_200_and_50",
        "rotation_threshold": -2.0,
        "label_he": "בינוני",
        "momentum_w10": 0.25,
        "momentum_w20": 0.40,
        "momentum_w50": 0.35,
    },
    "high": {
        "target_positions": 7,
        "rebalance_every": 5,
        "trail_pct": 0.75,
        "trail_atr_mult": 6.0,
        "invest_pct": 1.0,
        "allow_shorts": True,
        "bear_sell_all": True,
        "bear_trigger": "below_200_and_50",
        "rotation_threshold": -1.0,
        "label_he": "מוגבר",
        "momentum_w10": 0.22,
        "momentum_w20": 0.38,
        "momentum_w50": 0.40,
    },
}


class AgentSession:
    def __init__(
        self,
        *,
        mode: Mode = "sim",
        symbols: list[str] | None = None,
        deposit: float = 100_000.0,
        start_date: str | None = None,
        risk_level: RiskLevel = "medium",
    ):
        self.mode = mode
        self.symbols = [s.upper() for s in (symbols or DEFAULT_SYMBOLS)]
        self.initial_deposit = deposit
        self.cash = deposit
        self.positions: dict[str, dict[str, float]] = {}
        self.trades: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.step = 0
        self.running = False
        self.error: str | None = None
        self.risk_level: RiskLevel = risk_level if risk_level in RISK_PROFILES else "medium"

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

    def _sim_regt_short_margin(self) -> float:
        """Reg T style margin reserved on short stock (sim only), using mark or avg."""
        m = 0.0
        for sym, pos in self.positions.items():
            q = pos.get("qty", 0)
            if q >= 0:
                continue
            px = self._price_for(sym) or float(pos.get("avg_cost", 0))
            m += abs(q) * px * 0.5
        return m

    def _sim_buying_power(self) -> float:
        """Cash not tied as collateral for simulated shorts."""
        return max(0.0, self.cash - self._sim_regt_short_margin())

    def _ensure_broker_connected(self) -> Any:
        """Reuse one Alpaca client for paper/live ticks."""
        if self.mode not in ("paper", "live"):
            return None
        if self._broker is None:
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
        self.trades = []
        self.decisions = []
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
            if self._start_date:
                self._bar_index = self._find_start_index(self._start_date)
            else:
                self._bar_index = max(50, min(len(list(self._history.values())[0]) - 100, 50))
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
        max_len = min(len(b) for b in self._history.values()) if self._history else 0
        if self._bar_index >= max_len:
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

        from aibroker.agent.collector import sma as calc_sma, atr as calc_atr, rsi as calc_rsi

        spy_bars = self._history.get("SPY", ref_bars)
        spy_price = float(spy_bars[idx]["c"]) if idx < len(spy_bars) else 0
        spy_ma200 = calc_sma(spy_bars, idx, min(200, idx + 1))
        spy_ma50 = calc_sma(spy_bars, idx, 50)
        spy_rsi = calc_rsi(spy_bars, idx, 14)
        spy_roc20 = 0
        if idx >= 20 and len(spy_bars) > idx:
            spy_roc20 = (spy_price / float(spy_bars[idx - 20]["c"]) - 1) * 100

        above_200 = spy_price > spy_ma200 if spy_ma200 else True
        above_50 = spy_price > spy_ma50 if spy_ma50 else True

        bear_trigger = rp["bear_trigger"]
        if bear_trigger == "below_200":
            bear = not above_200
        else:
            bear = not above_200 and not above_50 and spy_roc20 < -5

        rankings = []
        for sym in self.symbols:
            bars = self._history.get(sym, [])
            if not bars or idx >= len(bars) or idx < 50:
                continue
            price = float(bars[idx]["c"])
            a14 = calc_atr(bars, idx, 14)
            r14 = calc_rsi(bars, idx, 14)
            ma20 = calc_sma(bars, idx, 20)
            ma50 = calc_sma(bars, idx, 50)
            if any(v is None for v in (a14, r14, ma20, ma50)):
                continue

            roc10 = (price / float(bars[idx - 10]["c"]) - 1) * 100 if idx >= 10 else 0
            roc20 = (price / float(bars[idx - 20]["c"]) - 1) * 100
            roc50 = (price / float(bars[idx - 50]["c"]) - 1) * 100 if idx >= 50 else roc20

            w10 = float(rp.get("momentum_w10", 0.25))
            w20 = float(rp.get("momentum_w20", 0.40))
            w50 = float(rp.get("momentum_w50", 0.35))
            momentum = roc10 * w10 + roc20 * w20 + roc50 * w50

            rankings.append({
                "symbol": sym, "price": price, "momentum": round(momentum, 2),
                "roc10": round(roc10, 2), "roc20": round(roc20, 2), "roc50": round(roc50, 2),
                "rsi": r14, "atr": a14, "ma20": ma20, "ma50": ma50,
                "above_ma50": price > ma50,
            })

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
            atr_val = r["atr"] or 1

            if q > 0:
                trail_base = max(avg * TRAIL_PCT, avg - TRAIL_ATR * atr_val)
                trail = self._trailing_stops.get(sym, trail_base)
                new_trail = max(trail, price - TRAIL_ATR * atr_val, price * TRAIL_PCT)
                self._trailing_stops[sym] = new_trail

                if price <= new_trail:
                    pnl_pct = (price / avg - 1) * 100 if avg > 0 else 0
                    actions.append((sym, "sell", abs(int(q)),
                                    f"סטופ נגרר ${new_trail:.0f} | {pnl_pct:+.1f}%"))
                    self._trailing_stops.pop(sym, None)

            elif q < 0:
                stop_up = 2.0 - TRAIL_PCT
                trail_base = min(avg * (1 + stop_up), avg + TRAIL_ATR * atr_val)
                trail = self._trailing_stops.get(sym, trail_base)
                new_trail = min(trail, price + TRAIL_ATR * atr_val, price * (1 + stop_up))
                self._trailing_stops[sym] = new_trail

                if price >= new_trail:
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
                        alloc = eq * 0.12
                        qty = max(1, int(alloc / r["price"]))
                        margin = qty * r["price"] * 0.5
                        bp = self._sim_buying_power()
                        if margin > bp * 0.25:
                            qty = max(1, int(bp * 0.25 / (r["price"] * 0.5)))
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
                for r in top:
                    sym = r["symbol"]
                    if sym in closed_syms:
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

        for sym, action, qty, reason in actions:
            self._execute_sim(sym, action, qty, reason, sim_date)

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
        max_len = min(len(b) for b in self._history.values()) if self._history else 0
        ref_bars = self._history.get("SPY", list(self._history.values())[0] if self._history else [])
        if self._bar_index >= max_len:
            self.running = False
            return self.status()

        sim_date = ref_bars[self._bar_index].get("date", str(self._bar_index)) if self._bar_index < len(ref_bars) else str(self._bar_index)

        snapshot = build_snapshot(
            symbols=self.symbols,
            history=self._history,
            bar_index=self._bar_index,
            positions=self.positions,
            cash=self.cash,
            initial_deposit=self.initial_deposit,
            news=[],
            sim_date=sim_date,
        )
        snapshot["risk_level"] = self.risk_level

        try:
            decision = think(snapshot, allowed_symbols=self.symbols)
        except Exception as e:
            log.error("Agent think error: %s", e)
            self.error = str(e)
            self._bar_index += 1
            self.step += 1
            return self.status()

        for act in decision.actions:
            self._execute_sim(act.symbol, act.action, act.quantity, act.reason, sim_date)

        self.decisions.append({
            "step": self.step,
            "date": sim_date,
            **decision.to_dict(),
        })

        self._bar_index += 1
        self.step += 1
        return self.status()

    def _execute_sim(self, symbol: str, action: str, qty: int, reason: str, date: str) -> None:
        if action == "hold" or qty <= 0:
            return
        bars = self._history.get(symbol, [])
        if not bars or self._bar_index >= len(bars):
            return
        price = float(bars[self._bar_index]["c"])

        if action == "buy":
            cost = price * qty
            bp = self._sim_buying_power()
            if cost > bp:
                qty = int(bp / price)
                if qty <= 0:
                    return
                cost = price * qty
            self.cash -= cost
            pos = self.positions.get(symbol, {"qty": 0, "avg_cost": 0, "opened": date})
            old_qty = pos["qty"]
            old_avg = pos["avg_cost"]
            if old_qty < 0:
                cover_qty = min(qty, abs(int(old_qty)))
                # מחיר הכיסוי כבר נוכה בשורה למעלה (self.cash -= cost). בתחילת השורט כבר
                # נזקפו למזומן תמורת המכירה — לא מוסיפים שוב ממוצע או רווח כאן (זה היה באג כפול).
                old_qty += cover_qty
                qty -= cover_qty
                if old_qty == 0:
                    old_avg = 0
            if qty > 0:
                new_qty = old_qty + qty
                new_avg = ((old_avg * old_qty) + (price * qty)) / new_qty if new_qty > 0 else price
                self.positions[symbol] = {"qty": new_qty, "avg_cost": new_avg, "opened": pos.get("opened", date)}
            elif old_qty != 0:
                self.positions[symbol] = {"qty": old_qty, "avg_cost": old_avg}
            else:
                self.positions.pop(symbol, None)
            self.trades.append({
                "step": self.step, "date": date, "symbol": symbol,
                "action": "buy", "price": round(price, 2), "qty": qty,
                "reason": reason,
            })

        elif action == "sell":
            pos = self.positions.get(symbol)
            if not pos or pos["qty"] <= 0:
                return
            qty = min(qty, int(pos["qty"]))
            if qty <= 0:
                return
            proceeds = price * qty
            self.cash += proceeds
            pos["qty"] -= qty
            if pos["qty"] <= 0:
                del self.positions[symbol]
            else:
                self.positions[symbol] = pos
            self.trades.append({
                "step": self.step, "date": date, "symbol": symbol,
                "action": "sell", "price": round(price, 2), "qty": qty,
                "reason": reason,
            })

        elif action == "short":
            margin = price * qty * 0.5
            bp = self._sim_buying_power()
            if margin > bp:
                qty = int(bp / (price * 0.5))
                if qty <= 0:
                    return
            self.cash += price * qty
            pos = self.positions.get(symbol, {"qty": 0, "avg_cost": 0, "opened": date})
            old_qty = pos["qty"]
            if old_qty > 0:
                sell_qty = min(qty, int(old_qty))
                old_qty -= sell_qty
                qty -= sell_qty
                if old_qty == 0:
                    self.positions.pop(symbol, None)
                else:
                    self.positions[symbol] = {"qty": old_qty, "avg_cost": pos["avg_cost"], "opened": pos.get("opened", date)}
            if qty > 0:
                cur_short = self.positions.get(symbol, {"qty": 0, "avg_cost": 0, "opened": date})
                cq = cur_short["qty"]
                ca = cur_short["avg_cost"]
                new_qty = cq - qty
                new_avg = ((abs(cq) * ca) + (qty * price)) / abs(new_qty) if new_qty != 0 else price
                self.positions[symbol] = {"qty": new_qty, "avg_cost": new_avg, "opened": cur_short.get("opened", date)}
            self.trades.append({
                "step": self.step, "date": date, "symbol": symbol,
                "action": "short", "price": round(price, 2), "qty": qty,
                "reason": reason,
            })

        elif action == "cover":
            pos = self.positions.get(symbol)
            if not pos or pos["qty"] >= 0:
                return
            short_qty = abs(int(pos["qty"]))
            qty = min(qty, short_qty)
            if qty <= 0:
                return
            cost = price * qty
            self.cash -= cost
            pos["qty"] += qty
            if pos["qty"] >= 0:
                self.positions.pop(symbol, None)
            else:
                self.positions[symbol] = pos
            self.trades.append({
                "step": self.step, "date": date, "symbol": symbol,
                "action": "cover", "price": round(price, 2), "qty": qty,
                "reason": reason,
            })

    def _tick_live(self) -> dict[str, Any]:
        self._request_news_refresh_async()

        self._history = load_history(self.symbols, bars=200)
        if self._history:
            self._bar_index = len(list(self._history.values())[0]) - 1

        snapshot = build_snapshot(
            symbols=self.symbols,
            history=self._history,
            bar_index=self._bar_index,
            positions=self.positions,
            cash=self.cash,
            initial_deposit=self.initial_deposit,
            news=self._news_cache,
        )
        snapshot["risk_level"] = self.risk_level

        try:
            broker = self._ensure_broker_connected()
        except Exception as e:
            log.error("Alpaca connect failed: %s", e)
            self.error = str(e)
            self.step += 1
            return self.status()

        try:
            acct = broker.get_account()
            if acct:
                self.cash = acct.get("cash_usd", self.cash)
            pos_list = broker.positions()
            self.positions = {}
            for p in pos_list:
                sym = p["symbol"]
                self.positions[sym] = {"qty": float(p["qty"]), "avg_cost": float(p["avg_cost"])}
        except Exception as e:
            log.warning("Pre-tick Alpaca sync failed: %s", e)

        try:
            decision = think(snapshot, allowed_symbols=self.symbols)
        except Exception as e:
            log.error("Agent think error: %s", e)
            self.error = str(e)
            self.step += 1
            return self.status()

        from aibroker.brokers.base import OrderIntent
        for act in decision.actions:
            side = act.action
            if side == "short":
                side = "sell"
            elif side == "cover":
                side = "buy"
            if side in ("buy", "sell") and act.quantity > 0:
                intent = OrderIntent(
                    symbol=act.symbol,
                    side=side,
                    quantity=act.quantity,
                )
                result = broker.place_order(intent)
                log.info("Order result: %s", result)
                self.trades.append({
                    "step": self.step,
                    "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "symbol": act.symbol,
                    "action": act.action,
                    "price": 0,
                    "qty": act.quantity,
                    "reason": act.reason,
                    "broker_ok": result.ok,
                    "broker_msg": result.message,
                })

        try:
            acct = broker.get_account()
            if acct:
                self.cash = acct.get("cash_usd", self.cash)
            pos_list = broker.positions()
            self.positions = {}
            for p in pos_list:
                sym = p["symbol"]
                self.positions[sym] = {"qty": float(p["qty"]), "avg_cost": float(p["avg_cost"])}
        except Exception as e:
            log.warning("Failed to sync account: %s", e)

        self.decisions.append({
            "step": self.step,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
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
            from aibroker.data.storage import save_session_end, save_trades, save_decisions
            eq = self.equity()
            pnl = eq - self.initial_deposit
            pnl_pct = (pnl / self.initial_deposit * 100) if self.initial_deposit > 0 else 0
            save_session_end(
                self._db_session_id, eq, pnl, pnl_pct,
                self.step, len(self.trades),
                self._equity_peak - self.initial_deposit,
                self._equity_trough - self.initial_deposit,
            )
            save_trades(self._db_session_id, self.trades)
            save_decisions(self._db_session_id, self.decisions[-50:])
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
            "last_trades": self.trades[-10:],
            "error": self.error,
        }
