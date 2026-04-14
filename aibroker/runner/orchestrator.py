from __future__ import annotations

import logging

from aibroker.brokers.factory import make_broker
from aibroker.config.schema import AppConfig
from aibroker.risk.gate import evaluate_intent
from aibroker.state.runtime import RuntimeState
from aibroker.strategies.simple_rules import SimpleRulesStrategy

log = logging.getLogger(__name__)


def run_once(cfg: AppConfig, *, connect_broker: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    state = RuntimeState(
        profile_name=cfg.profile_name,
        account_mode=cfg.account_mode,
        dry_run=cfg.execution.dry_run,
        kill_switch=cfg.risk.kill_switch,
    )
    strat = SimpleRulesStrategy()
    intents = strat.generate_signals(cfg, state)
    log.info("Signals produced %d intent(s)", len(intents))

    broker = None
    if connect_broker and not cfg.execution.dry_run:
        broker = make_broker(cfg)
        broker.connect()
        try:
            state.positions = broker.positions()
            state.open_orders = broker.open_orders()
        except Exception:
            broker.disconnect()
            broker = None
            raise
    elif connect_broker and cfg.execution.dry_run:
        log.info("dry_run=true — skipping broker connect")

    try:
        for intent in intents:
            d = evaluate_intent(cfg, state, intent)
            if not d.allowed:
                log.warning("Risk blocked: %s — %s", intent.symbol, d.reason)
                continue
            if cfg.execution.dry_run:
                log.info("[dry_run] would place %s %s %s", intent.side, intent.quantity, intent.symbol)
            elif broker is not None:
                try:
                    res = broker.place_order(intent)
                    log.info("Order result: %s", res.message)
                except Exception as e:
                    log.error("Failed to place order for %s: %s", intent.symbol, e)
            else:
                b = make_broker(cfg)
                b.connect()
                try:
                    res = b.place_order(intent)
                    log.info("Order result: %s", res.message)
                finally:
                    b.disconnect()
    finally:
        if broker is not None:
            broker.disconnect()
