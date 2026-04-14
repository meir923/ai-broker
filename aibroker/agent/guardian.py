"""Guardian — background safety monitor. Checks portfolio health every 30s."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

import httpx

log = logging.getLogger(__name__)

GUARDIAN_LIMITS: dict[str, dict[str, float]] = {
    "low": {"daily_loss_pct": 3.0, "position_loss_pct": 5.0, "drawdown_pct": 8.0},
    "medium": {"daily_loss_pct": 5.0, "position_loss_pct": 8.0, "drawdown_pct": 12.0},
    "high": {"daily_loss_pct": 8.0, "position_loss_pct": 12.0, "drawdown_pct": 18.0},
}


def _alpaca_headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
        "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", ""),
    }


def _alpaca_base(paper: bool) -> str:
    return "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"


class Guardian:
    def __init__(self, get_session: Callable[[], Any], stop_session: Callable[[], None]):
        self._get_session = get_session
        self._stop_session = stop_session
        self._thread: threading.Thread | None = None
        self._running = False
        self._check_interval = 30

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="guardian")
        self._thread.start()
        log.info("Guardian started (checking every %ds)", self._check_interval)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        log.info("Guardian stopped")

    def _loop(self) -> None:
        while self._running:
            try:
                self._check()
            except Exception as e:
                log.error("Guardian check error: %s", e)
            time.sleep(self._check_interval)

    def _check(self) -> None:
        session = self._get_session()
        if session is None or not session.running:
            return
        if session.mode not in ("paper", "live"):
            return

        headers = _alpaca_headers()
        if not headers["APCA-API-KEY-ID"]:
            return
        base = _alpaca_base(session.mode == "paper")

        try:
            acct_r = httpx.get(f"{base}/v2/account", headers=headers, timeout=10)
            if acct_r.status_code != 200:
                return
            acct = acct_r.json()
            pos_r = httpx.get(f"{base}/v2/positions", headers=headers, timeout=10)
            positions = pos_r.json() if pos_r.status_code == 200 else []
        except Exception as e:
            log.warning("Guardian: check failed: %s", e)
            return

        equity = float(acct.get("equity", 0))
        deposit = session.initial_deposit
        if deposit <= 0:
            return

        limits = GUARDIAN_LIMITS.get(session.risk_level, GUARDIAN_LIMITS["medium"])

        pnl_pct = (equity / deposit - 1) * 100
        if pnl_pct < -limits["daily_loss_pct"]:
            self._emergency_close(
                session, base, headers,
                f"הפסד יומי {pnl_pct:.1f}% חורג ממגבלת {limits['daily_loss_pct']}%",
            )
            return

        peak = session._equity_peak
        if peak > 0:
            drawdown_pct = (1 - equity / peak) * 100
            if drawdown_pct > limits["drawdown_pct"]:
                self._emergency_close(
                    session, base, headers,
                    f"ירידה מהשיא {drawdown_pct:.1f}% חורגת ממגבלת {limits['drawdown_pct']}%",
                )
                return

        for p in positions:
            upl = float(p.get("unrealized_pl", 0))
            avg = float(p.get("avg_entry_price", 0))
            qty = abs(float(p.get("qty", 0)))
            if avg > 0 and qty > 0:
                cost = avg * qty
                loss_pct = abs(upl / cost * 100) if upl < 0 else 0
                if loss_pct > limits["position_loss_pct"]:
                    self._close_position(session, base, headers, p,
                                         f"הפסד בפוזיציה {loss_pct:.1f}% > {limits['position_loss_pct']}%")

    def _emergency_close(self, session: Any, base: str, headers: dict, reason: str) -> None:
        log.warning("GUARDIAN EMERGENCY: %s — closing all positions", reason)

        from aibroker.agent.alerts import alert_stop_loss
        eq = session.equity()
        pnl = eq - session.initial_deposit
        alert_stop_loss(reason, eq, pnl)

        try:
            httpx.delete(f"{base}/v2/positions", headers=headers, timeout=10)
            log.info("All positions liquidated via API")
        except Exception as e:
            log.error("Failed to liquidate positions: %s", e)

        session.running = False
        self._stop_session()
        log.warning("Agent stopped by guardian: %s", reason)

    def _close_position(self, session: Any, base: str, headers: dict, position: dict, reason: str) -> None:
        sym = position.get("symbol", "")
        log.warning("GUARDIAN: closing %s — %s", sym, reason)

        try:
            httpx.delete(f"{base}/v2/positions/{sym}", headers=headers, timeout=10)
            log.info("Position %s closed by guardian", sym)

            from aibroker.agent.alerts import send_alert
            send_alert(f"Guardian סגר {sym}", reason)
        except Exception as e:
            log.error("Failed to close position %s: %s", sym, e)
