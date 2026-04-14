from __future__ import annotations

from typing import Protocol

from aibroker.brokers.base import OrderIntent
from aibroker.config.schema import AppConfig
from aibroker.state.runtime import RuntimeState


class Strategy(Protocol):
    def generate_signals(self, cfg: AppConfig, state: RuntimeState) -> list[OrderIntent]:
        ...


def generate_signals(strategy: Strategy, cfg: AppConfig, state: RuntimeState) -> list[OrderIntent]:
    return strategy.generate_signals(cfg, state)
