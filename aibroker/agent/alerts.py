"""Alert system — sends notifications via Telegram."""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _telegram_config() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return token, chat_id


def is_configured() -> bool:
    token, chat_id = _telegram_config()
    return bool(token and chat_id)


def send_alert(title: str, message: str) -> bool:
    token, chat_id = _telegram_config()
    if not token or not chat_id:
        log.debug("Telegram not configured, skipping alert: %s", title)
        return False

    text = f"*{title}*\n{message}"
    try:
        import httpx
        r = httpx.post(
            _TELEGRAM_API.format(token=token),
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if r.status_code == 200:
            log.info("Alert sent: %s", title)
            return True
        else:
            log.warning("Telegram alert failed (%d): %s", r.status_code, r.text[:200])
            return False
    except Exception as e:
        log.warning("Telegram alert error: %s", e)
        return False


def alert_agent_started(mode: str, risk_level: str, symbols: int, deposit: float) -> None:
    send_alert(
        "סוכן AI הופעל",
        f"מצב: {mode} | סיכון: {risk_level}\n"
        f"סימבולים: {symbols} | הפקדה: ${deposit:,.0f}",
    )


def alert_agent_stopped(equity: float, pnl: float, pnl_pct: float, reason: str = "") -> None:
    sign = "+" if pnl >= 0 else ""
    send_alert(
        "סוכן AI נעצר",
        f"הון: ${equity:,.0f} | רווח: {sign}${pnl:,.0f} ({sign}{pnl_pct:.1f}%)"
        + (f"\nסיבה: {reason}" if reason else ""),
    )


def alert_stop_loss(trigger: str, equity: float, pnl: float) -> None:
    send_alert(
        "STOP LOSS הופעל",
        f"סיבה: {trigger}\nהון: ${equity:,.0f} | הפסד: ${pnl:,.0f}\n"
        "כל הפוזיציות נסגרו.",
    )


def alert_trade(symbol: str, action: str, qty: int, price: float, reason: str) -> None:
    actions_he = {"buy": "קנייה", "sell": "מכירה", "short": "שורט", "cover": "כיסוי"}
    send_alert(
        f"עסקה: {actions_he.get(action, action)} {symbol}",
        f"{qty}x @ ${price:.2f}\nסיבה: {reason}",
    )


def alert_daily_summary(equity: float, pnl: float, pnl_pct: float,
                        positions: int, trades_today: int) -> None:
    sign = "+" if pnl >= 0 else ""
    send_alert(
        "סיכום יומי",
        f"הון: ${equity:,.0f} | רווח: {sign}${pnl:,.0f} ({sign}{pnl_pct:.1f}%)\n"
        f"פוזיציות: {positions} | עסקאות היום: {trades_today}",
    )


def alert_error(error: str) -> None:
    send_alert("שגיאה בסוכן", error)
