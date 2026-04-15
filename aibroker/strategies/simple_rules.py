"""
SwingPortfolioManager — Donchian Breakout with:
  - Dynamic per-symbol leverage (1x-5x) via RegimeDetector
  - Leverage-aware position sizing (buying power scales with leverage)
  - News sentiment filter (amplify/dampen/skip)
  - 3xATR stop / 4xATR target / 3xATR trailing stop
  - Pyramiding: +75% shares after 1.5xATR favorable move
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from aibroker.brokers.base import OrderIntent
from aibroker.strategies.regime import RegimeDetector
from aibroker.strategies.swing import (
    DonchianBreak,
    Signal,
    SwingStrategy,
    compute_atr,
)

log = logging.getLogger(__name__)

STRATEGY_NAMES = ["donchian_break"]

RISK_PCT = 0.01
MAX_POS_PCT_EQUITY = 0.12
MAX_PORTFOLIO_EXPOSURE = 1.5
MAX_CONCURRENT_POSITIONS = 6
PYRAMID_ATR = 1.5
PYRAMID_ADD_RATIO = 0.5

SENTIMENT_BOOST = 1.3
SENTIMENT_THRESHOLD = 0.3


@dataclass
class _PositionMeta:
    """Per-position bookkeeping for risk management."""
    entry_px: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    initial_stop: float = 0.0
    side: str = "flat"
    strategy: str = ""
    entry_bar: int = 0
    highest_since_entry: float = 0.0
    lowest_since_entry: float = float("inf")
    pyramided: bool = False
    exit_px: float = 0.0
    symbol_leverage: float = 2.0


class SwingPortfolioManager:
    """Donchian Breakout with dynamic leverage, sentiment, and pyramiding."""

    def __init__(self) -> None:
        self._strats: dict[str, SwingStrategy] = {}
        self._meta: dict[str, _PositionMeta] = {}
        self._regime = RegimeDetector()

    def _ensure_strat(self, sym: str, symbols: list[str]) -> SwingStrategy:
        if sym not in self._strats:
            self._strats[sym] = DonchianBreak()
        return self._strats[sym]

    def _get_meta(self, sym: str) -> _PositionMeta:
        return self._meta.setdefault(sym, _PositionMeta())

    def evaluate_all(
        self,
        bar_idx: int,
        history: dict[str, list[dict[str, Any]]],
        positions: dict[str, dict[str, float]],
        equity: float,
        symbols: list[str],
        leverage: float = 2.0,
        cash: float = 0.0,
        sentiment: dict[str, float] | None = None,
    ) -> list[tuple[OrderIntent, str]]:
        exits: list[tuple[OrderIntent, str]] = []
        entries: list[tuple[OrderIntent, str]] = []
        pyramids: list[tuple[OrderIntent, str]] = []

        self._regime.max_lev = leverage
        news = sentiment or {}

        total_exposure = 0.0
        open_count = 0
        for sym in symbols:
            h = float(positions.get(sym, {}).get("qty", 0.0))
            if abs(h) > 1e-9:
                bars_s = history.get(sym)
                if bars_s and bar_idx < len(bars_s):
                    total_exposure += abs(h) * float(bars_s[bar_idx]["c"])
                open_count += 1
        max_notional = equity * MAX_PORTFOLIO_EXPOSURE

        for sym in symbols:
            bars = history.get(sym)
            if not bars or bar_idx >= len(bars):
                continue

            held = float(positions.get(sym, {}).get("qty", 0.0))
            meta = self._get_meta(sym)
            strat = self._ensure_strat(sym, symbols)
            cur_bar = bars[bar_idx]
            px_close = float(cur_bar["c"])
            bar_h = float(cur_bar["h"])
            bar_l = float(cur_bar["l"])

            pos_side: str = "flat"
            if held > 1e-9:
                pos_side = "long"
            elif held < -1e-9:
                pos_side = "short"

            atr_vals = compute_atr(bars[: bar_idx + 1], 14)
            cur_atr = atr_vals[bar_idx] if bar_idx < len(atr_vals) and atr_vals[bar_idx] is not None and atr_vals[bar_idx] > 0 else px_close * 0.015

            sym_lev = self._regime.get_leverage(sym, bars, bar_idx, equity)
            meta.symbol_leverage = sym_lev

            # --- Trailing stop ---
            if pos_side == "long":
                meta.highest_since_entry = max(meta.highest_since_entry, bar_h)
                trailing = meta.highest_since_entry - 3.0 * cur_atr
                if trailing > meta.stop:
                    meta.stop = trailing

            if pos_side == "short":
                meta.lowest_since_entry = min(meta.lowest_since_entry, bar_l)
                trailing = meta.lowest_since_entry + 3.0 * cur_atr
                if trailing < meta.stop:
                    meta.stop = trailing

            bar_o = float(cur_bar["o"])

            # --- Stop-loss / Take-profit with gap-aware fill prices ---
            if pos_side == "long" and meta.stop > 0 and bar_l <= meta.stop:
                meta.exit_px = min(meta.stop, bar_o) if bar_o < meta.stop else meta.stop
                exits.append((
                    OrderIntent(symbol=sym, side="sell", quantity=abs(held), order_type="market"),
                    f"{meta.strategy}|stop_loss",
                ))
                meta.side = "flat"
                meta.pyramided = False
                continue

            if pos_side == "long" and meta.target > 0 and bar_h >= meta.target:
                meta.exit_px = max(meta.target, bar_o) if bar_o > meta.target else meta.target
                exits.append((
                    OrderIntent(symbol=sym, side="sell", quantity=abs(held), order_type="market"),
                    f"{meta.strategy}|take_profit",
                ))
                meta.side = "flat"
                meta.pyramided = False
                continue

            if pos_side == "short" and meta.stop > 0 and bar_h >= meta.stop:
                meta.exit_px = max(meta.stop, bar_o) if bar_o > meta.stop else meta.stop
                exits.append((
                    OrderIntent(symbol=sym, side="buy", quantity=abs(held), order_type="market"),
                    f"{meta.strategy}|stop_loss",
                ))
                meta.side = "flat"
                meta.pyramided = False
                continue

            if pos_side == "short" and meta.target > 0 and bar_l <= meta.target:
                meta.exit_px = min(meta.target, bar_o) if bar_o < meta.target else meta.target
                exits.append((
                    OrderIntent(symbol=sym, side="buy", quantity=abs(held), order_type="market"),
                    f"{meta.strategy}|take_profit",
                ))
                meta.side = "flat"
                meta.pyramided = False
                continue

            # --- Pyramiding: add to winners (limit to 30% of cash) ---
            if pos_side != "flat" and not meta.pyramided and abs(held) > 0:
                move = (px_close - meta.entry_px) if pos_side == "long" else (meta.entry_px - px_close)
                if move >= PYRAMID_ATR * cur_atr:
                    add_qty = max(1.0, math.floor(abs(held) * PYRAMID_ADD_RATIO))
                    margin_needed = add_qty * px_close / sym_lev
                    if cash > 0 and margin_needed < cash * 0.3:
                        side_str = "buy" if pos_side == "long" else "sell"
                        pyramids.append((
                            OrderIntent(symbol=sym, side=side_str, quantity=float(add_qty), order_type="market"),
                            f"{meta.strategy}|pyramid",
                        ))
                        if pos_side == "long":
                            meta.stop = max(meta.stop, meta.entry_px)
                        else:
                            meta.stop = min(meta.stop, meta.entry_px)
                        meta.pyramided = True
                    continue

            # --- Strategy signal for new entry ---
            sig: Signal | None = strat.evaluate(bars, bar_idx, pos_side)
            if sig is None:
                continue

            # --- Sentiment filter ---
            sent = news.get(sym, 0.0)
            effective_lev = sym_lev

            if sig.action == "buy":
                if sent < -SENTIMENT_THRESHOLD:
                    continue
                if sent > SENTIMENT_THRESHOLD:
                    effective_lev = min(effective_lev * SENTIMENT_BOOST, self._regime.max_lev)
            elif sig.action == "sell":
                if sent > SENTIMENT_THRESHOLD:
                    continue
                if sent < -SENTIMENT_THRESHOLD:
                    effective_lev = min(effective_lev * SENTIMENT_BOOST, self._regime.max_lev)

            if open_count >= MAX_CONCURRENT_POSITIONS:
                continue
            if total_exposure >= max_notional:
                continue

            atr = cur_atr
            risk_per_trade = equity * RISK_PCT
            max_pos_value = equity * MAX_POS_PCT_EQUITY
            remaining_capacity = max(0, max_notional - total_exposure)

            if sig.action == "buy" and pos_side == "flat":
                stop_dist = max(3.0 * atr, px_close * 0.008)
                qty = max(1.0, math.floor(risk_per_trade / stop_dist))
                qty = min(qty, math.floor(max_pos_value / px_close))
                qty = min(qty, math.floor(remaining_capacity / px_close)) if remaining_capacity > 0 else qty
                if qty < 1:
                    continue
                stop = px_close - stop_dist
                target = px_close + 4.0 * atr
                entries.append((
                    OrderIntent(symbol=sym, side="buy", quantity=float(qty), order_type="market"),
                    strat.name,
                ))
                meta.entry_px = px_close
                meta.stop = stop
                meta.target = target
                meta.initial_stop = stop
                meta.side = "long"
                meta.strategy = strat.name
                meta.entry_bar = bar_idx
                meta.highest_since_entry = px_close
                meta.lowest_since_entry = px_close
                meta.pyramided = False
                meta.exit_px = 0.0
                meta.symbol_leverage = effective_lev
                total_exposure += qty * px_close
                open_count += 1

            elif sig.action == "sell" and pos_side == "flat":
                stop_dist = max(3.0 * atr, px_close * 0.008)
                qty = max(1.0, math.floor(risk_per_trade / stop_dist))
                qty = min(qty, math.floor(max_pos_value / px_close))
                qty = min(qty, math.floor(remaining_capacity / px_close)) if remaining_capacity > 0 else qty
                if qty < 1:
                    continue
                stop = px_close + stop_dist
                target = px_close - 4.0 * atr
                entries.append((
                    OrderIntent(symbol=sym, side="sell", quantity=float(qty), order_type="market"),
                    strat.name,
                ))
                meta.entry_px = px_close
                meta.stop = stop
                meta.target = target
                meta.initial_stop = stop
                meta.side = "short"
                meta.strategy = strat.name
                meta.entry_bar = bar_idx
                meta.highest_since_entry = px_close
                meta.lowest_since_entry = px_close
                meta.pyramided = False
                meta.exit_px = 0.0
                meta.symbol_leverage = effective_lev
                total_exposure += qty * px_close
                open_count += 1

        return exits + pyramids + entries

    def get_meta(self, sym: str) -> dict[str, Any]:
        m = self._meta.get(sym)
        if not m:
            return {}
        return {
            "entry_px": m.entry_px,
            "stop": m.stop,
            "target": m.target,
            "initial_stop": m.initial_stop,
            "side": m.side,
            "strategy": m.strategy,
            "entry_bar": m.entry_bar,
            "pyramided": m.pyramided,
            "exit_px": m.exit_px,
            "symbol_leverage": m.symbol_leverage,
        }

    def record_open_fill_price(self, sym: str, px: float) -> None:
        """After a pending entry fills at next bar open, persist the actual fill price on meta."""
        self._get_meta(sym.upper()).entry_px = float(px)


# ---- Deprecated legacy aliases ----
# These exist only for backward compatibility with tests and imports.
# The real trading engine lives in agent/loop.py (_tick_sim / _tick_live).

import warnings as _warnings

StrategyPicker = SwingPortfolioManager


class _DeprecatedStub:
    """Stub strategy that returns no signals. Do not use for new code."""

    def evaluate(self, *a: Any, **kw: Any) -> None:
        return None


class SMAStrategy(_DeprecatedStub):
    name = "sma_crossover"


class MomentumStrategy(_DeprecatedStub):
    name = "momentum"


class MeanReversionStrategy(_DeprecatedStub):
    name = "mean_reversion"


class ScalperStrategy(_DeprecatedStub):
    name = "scalper"


class SimpleRulesStrategy:
    """Deprecated: returns no signals. Use AgentSession instead."""

    def generate_signals(self, cfg: Any, state: Any) -> list:
        _warnings.warn(
            "SimpleRulesStrategy.generate_signals() always returns []. "
            "Use AgentSession._tick_sim() for actual trading.",
            DeprecationWarning,
            stacklevel=2,
        )
        return []
