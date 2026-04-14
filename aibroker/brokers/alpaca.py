"""Alpaca Paper/Live broker adapter using the alpaca-py SDK."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from aibroker.brokers.base import BrokerClient, OrderIntent, OrderResult

log = logging.getLogger(__name__)

_quote_cache: dict[str, tuple[float, float]] = {}
_QUOTE_TTL = 4.0

_hist_data_client: Any = None
_hist_data_client_keys: tuple[str, str] | None = None


def _alpaca_keys() -> tuple[str, str]:
    api_key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    return api_key, secret


def alpaca_keys_set() -> bool:
    k, s = _alpaca_keys()
    return bool(k and s)


def _get_stock_historical_client() -> Any | None:
    """Reuse one StockHistoricalDataClient per key pair (quotes / prices)."""
    global _hist_data_client, _hist_data_client_keys
    api_key, secret_key = _alpaca_keys()
    if not api_key or not secret_key:
        return None
    keys = (api_key, secret_key)
    if _hist_data_client is not None and _hist_data_client_keys == keys:
        return _hist_data_client
    from alpaca.data.historical import StockHistoricalDataClient

    _hist_data_client = StockHistoricalDataClient(api_key, secret_key)
    _hist_data_client_keys = keys
    return _hist_data_client


def fetch_alpaca_quotes(symbols: list[str]) -> dict[str, float]:
    """Fetch latest ask/bid midpoint for symbols. Returns {SYM: price}. Cached briefly."""
    api_key, secret_key = _alpaca_keys()
    if not api_key or not secret_key:
        return {}

    now = time.monotonic()
    needed: list[str] = []
    result: dict[str, float] = {}
    for s in symbols:
        s = s.upper()
        if s in _quote_cache:
            ts, px = _quote_cache[s]
            if now - ts < _QUOTE_TTL:
                result[s] = px
                continue
        needed.append(s)

    if not needed:
        return result

    try:
        from alpaca.data.requests import StockLatestQuoteRequest

        client = _get_stock_historical_client()
        if client is None:
            return result
        req = StockLatestQuoteRequest(symbol_or_symbols=needed)
        quotes = client.get_stock_latest_quote(req)

        for sym, q in quotes.items():
            ask = float(q.ask_price) if q.ask_price else 0.0
            bid = float(q.bid_price) if q.bid_price else 0.0
            if ask > 0 and bid > 0:
                px = round((ask + bid) / 2.0, 4)
            elif ask > 0:
                px = ask
            elif bid > 0:
                px = bid
            else:
                continue
            sym_u = str(sym).upper()
            _quote_cache[sym_u] = (now, px)
            result[sym_u] = px
    except Exception as exc:
        log.warning("Alpaca quote fetch failed: %s", exc)

    return result


class AlpacaBrokerClient(BrokerClient):
    def __init__(self, *, paper: bool = True) -> None:
        self._paper = paper
        self._trading_client: Any = None

    def connect(self) -> None:
        api_key, secret_key = _alpaca_keys()
        if not api_key or not secret_key:
            raise RuntimeError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env"
            )
        from alpaca.trading.client import TradingClient

        self._trading_client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=self._paper,
        )
        acct = self._trading_client.get_account()
        log.info(
            "Alpaca connected (%s) — equity $%s, buying power $%s",
            "paper" if self._paper else "LIVE",
            acct.equity,
            acct.buying_power,
        )

    def disconnect(self) -> None:
        self._trading_client = None

    def get_account(self) -> dict[str, Any]:
        if self._trading_client is None:
            return {}
        acct = self._trading_client.get_account()
        return {
            "equity_usd": float(acct.equity),
            "cash_usd": float(acct.cash),
            "buying_power_usd": float(acct.buying_power),
            "portfolio_value_usd": float(acct.portfolio_value),
            "pnl_usd": float(acct.equity) - float(acct.last_equity),
            "status": str(acct.status),
            "paper": self._paper,
        }

    def positions(self) -> list[dict[str, Any]]:
        if self._trading_client is None:
            return []
        raw = self._trading_client.get_all_positions()
        out: list[dict[str, Any]] = []
        for p in raw:
            out.append(
                {
                    "symbol": str(p.symbol),
                    "qty": float(p.qty),
                    "avg_cost": float(p.avg_entry_price),
                    "market_value": float(p.market_value),
                    "unrealized_pl": float(p.unrealized_pl),
                    "current_price": float(p.current_price),
                }
            )
        return out

    def open_orders(self) -> list[dict[str, Any]]:
        if self._trading_client is None:
            return []
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        raw = self._trading_client.get_orders(filter=req)
        out: list[dict[str, Any]] = []
        for o in raw:
            out.append(
                {
                    "order_id": str(o.id),
                    "symbol": str(o.symbol),
                    "side": str(o.side),
                    "qty": str(o.qty),
                    "status": str(o.status),
                    "type": str(o.type),
                }
            )
        return out

    def _is_extended_hours(self) -> bool:
        try:
            clock = self._trading_client.get_clock()
            return not clock.is_open
        except Exception:
            return False

    def place_order(self, intent: OrderIntent) -> OrderResult:
        if self._trading_client is None:
            return OrderResult(ok=False, message="Not connected to Alpaca")
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        side = OrderSide.BUY if intent.side == "buy" else OrderSide.SELL
        extended = self._is_extended_hours()

        try:
            if extended:
                price = self._get_current_price(intent.symbol)
                if price <= 0:
                    return OrderResult(ok=False, message=f"Cannot get price for {intent.symbol}")
                slippage = 0.005
                limit_px = round(price * (1 + slippage) if side == OrderSide.BUY else price * (1 - slippage), 2)
                req = LimitOrderRequest(
                    symbol=intent.symbol.upper(),
                    qty=intent.quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=limit_px,
                    extended_hours=True,
                )
                log.info("Extended hours limit order: %s %s %s @ $%.2f", side, intent.quantity, intent.symbol, limit_px)
            elif intent.order_type == "limit" and intent.limit_price:
                tif = TimeInForce.DAY
                req = LimitOrderRequest(
                    symbol=intent.symbol.upper(),
                    qty=intent.quantity,
                    side=side,
                    time_in_force=tif,
                    limit_price=intent.limit_price,
                )
            else:
                tif = TimeInForce.DAY
                req = MarketOrderRequest(
                    symbol=intent.symbol.upper(),
                    qty=intent.quantity,
                    side=side,
                    time_in_force=tif,
                )
            order = self._trading_client.submit_order(order_data=req)
            return OrderResult(
                ok=True,
                message=f"Submitted {intent.side} {intent.quantity} {intent.symbol}" + (" [extended]" if extended else ""),
                broker_order_id=str(order.id),
                raw={"status": str(order.status), "filled_avg_price": str(order.filled_avg_price or "")},
            )
        except Exception as exc:
            log.error("Alpaca order failed: %s", exc)
            return OrderResult(ok=False, message=str(exc))

    def _get_current_price(self, symbol: str) -> float:
        try:
            from alpaca.data.requests import StockLatestQuoteRequest
            client = _get_stock_historical_client()
            if client is None:
                return 0.0
            req = StockLatestQuoteRequest(symbol_or_symbols=[symbol.upper()])
            quotes = client.get_stock_latest_quote(req)
            q = quotes.get(symbol.upper())
            if q:
                ask = float(q.ask_price) if q.ask_price else 0
                bid = float(q.bid_price) if q.bid_price else 0
                if ask > 0 and bid > 0:
                    return (ask + bid) / 2
                return ask or bid
        except Exception as e:
            log.warning("Failed to get price for %s: %s", symbol, e)
        return 0.0
