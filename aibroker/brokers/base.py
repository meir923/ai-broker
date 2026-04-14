from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field

Side = Literal["buy", "sell"]


class OrderIntent(BaseModel):
    symbol: str
    side: Side
    quantity: float = Field(gt=0)
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = None
    time_in_force: str = "DAY"
    client_tag: str = ""


class OrderResult(BaseModel):
    ok: bool
    message: str
    broker_order_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class BrokerClient(ABC):
    @abstractmethod
    def connect(self) -> None:
        pass

    @abstractmethod
    def disconnect(self) -> None:
        pass

    @abstractmethod
    def positions(self) -> list[dict[str, Any]]:
        pass

    @abstractmethod
    def open_orders(self) -> list[dict[str, Any]]:
        pass

    @abstractmethod
    def place_order(self, intent: OrderIntent) -> OrderResult:
        pass
