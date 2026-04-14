from __future__ import annotations

from aibroker.brokers.alpaca import AlpacaBrokerClient
from aibroker.brokers.base import BrokerClient
from aibroker.brokers.ibkr import IbkrBrokerClient
from aibroker.config.schema import AppConfig


def make_broker(cfg: AppConfig) -> BrokerClient:
    if cfg.broker == "ibkr":
        return IbkrBrokerClient(cfg)
    if cfg.broker == "alpaca":
        return AlpacaBrokerClient(paper=cfg.account_mode == "paper")
    raise ValueError(f"Unknown broker: {cfg.broker}")
