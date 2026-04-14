from __future__ import annotations

import logging
from typing import Any

from aibroker.brokers.base import BrokerClient, OrderIntent, OrderResult
from aibroker.config.schema import AppConfig

log = logging.getLogger(__name__)


class IbkrBrokerClient(BrokerClient):
    """IBKR via ib_insync when installed; otherwise informative error on connect."""

    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self._ib = None

    def connect(self) -> None:
        try:
            from ib_insync import IB  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                'Install optional dependency: pip install "aibroker[ibkr]" (ib-insync).'
            ) from e
        self._ib = IB()
        self._ib.connect(
            self._cfg.ibkr.host,
            self._cfg.ibkr.port,
            clientId=self._cfg.ibkr.client_id,
        )
        log.info("Connected to IBKR at %s:%s", self._cfg.ibkr.host, self._cfg.ibkr.port)

    def disconnect(self) -> None:
        if self._ib is not None:
            self._ib.disconnect()
            self._ib = None

    def positions(self) -> list[dict[str, Any]]:
        if self._ib is None:
            return []
        out: list[dict[str, Any]] = []
        for p in self._ib.positions():
            c = p.contract
            sym = getattr(c, "symbol", "") or ""
            out.append(
                {
                    "symbol": sym,
                    "quantity": float(p.position),
                    "avg_cost": float(p.avgCost),
                }
            )
        return out

    def open_orders(self) -> list[dict[str, Any]]:
        if self._ib is None:
            return []
        return [
            {
                "order_id": str(o.order.orderId),
                "symbol": getattr(o.contract, "symbol", ""),
                "status": o.orderStatus.status,
            }
            for o in self._ib.openTrades()
        ]

    def place_order(self, intent: OrderIntent) -> OrderResult:
        if self._ib is None:
            return OrderResult(ok=False, message="Not connected")
        # Minimal stub: real implementation must map contract, exchange, paper account, etc.
        return OrderResult(
            ok=False,
            message="place_order not fully implemented — use dry_run until wired",
        )
