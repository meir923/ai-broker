"""Save and restore agent state across server restarts."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def save_state(session: Any) -> None:
    """Persist current agent state to SQLite. Called after every tick."""
    try:
        from aibroker.data.storage import save_agent_state
        save_agent_state(
            session_id=session._db_session_id,
            mode=session.mode,
            risk_level=session.risk_level,
            deposit=session.initial_deposit,
            symbols=session.symbols,
            cash=session.cash,
            step=session.step,
            running=session.running,
            positions=session.positions,
            equity_peak=session._equity_peak,
            equity_trough=session._equity_trough,
        )
    except Exception as e:
        log.warning("Failed to save agent state: %s", e)


def restore_session() -> Any | None:
    """Rebuild AgentSession from DB + Alpaca on server startup. Returns session or None."""
    try:
        from aibroker.data.storage import load_agent_state
        state = load_agent_state()
        if not state or not state.get("running"):
            return None

        mode = state["mode"]
        if mode not in ("paper", "live"):
            log.info("Saved session is sim mode, not restoring")
            from aibroker.data.storage import clear_agent_state
            clear_agent_state()
            return None

        log.info("Found saved agent state: mode=%s, step=%s, session_id=%s",
                 mode, state["step"], state["session_id"])

        from aibroker.brokers.alpaca import alpaca_keys_set
        if not alpaca_keys_set():
            log.warning("Cannot restore session: Alpaca keys not set")
            return None

        from aibroker.brokers.alpaca import AlpacaBrokerClient
        broker = AlpacaBrokerClient(paper=(mode == "paper"))
        try:
            broker.connect()
            acct = broker.get_account()
            alpaca_positions = broker.positions()
            broker.disconnect()
        except Exception as e:
            log.error("Cannot restore session: Alpaca connection failed: %s", e)
            return None

        from aibroker.agent.loop import AgentSession
        session = AgentSession(
            mode=mode,
            symbols=state["symbols"],
            deposit=state["deposit"],
            risk_level=state["risk_level"],
        )
        session._db_session_id = state["session_id"]
        session.step = state["step"]
        session.running = True
        session._equity_peak = state.get("equity_peak", state["deposit"])
        session._equity_trough = state.get("equity_trough", state["deposit"])

        session.cash = float(acct.get("cash_usd", state["cash"]))
        session.positions = {}
        for p in alpaca_positions:
            sym = p["symbol"]
            qty = float(p["qty"])
            avg = float(p["avg_cost"])
            session.positions[sym] = {"qty": qty, "avg_cost": avg, "opened": ""}

        from aibroker.data.historical import load_history
        session._history = load_history(session.symbols, bars=200)
        if session._history:
            session._bar_index = len(list(session._history.values())[0]) - 1

        eq = session.equity()
        log.info("Agent session restored: equity=$%.2f, %d positions, step=%d",
                 eq, len(session.positions), session.step)

        return session

    except Exception as e:
        log.error("Failed to restore agent session: %s", e)
        return None


def mark_stopped() -> None:
    """Clear the running flag in saved state."""
    try:
        from aibroker.data.storage import clear_agent_state
        clear_agent_state()
    except Exception as e:
        log.warning("Failed to clear agent state: %s", e)
