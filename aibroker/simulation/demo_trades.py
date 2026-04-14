"""
Demo trade session: real HTTP API + real risk gate (evaluate_intent), dry_run execution only.
No broker connection — fills are simulated and recorded in the response.

Supports a persistent in-memory "live stream" of additional demo trades after the initial batch
(single-process local dashboard; resets when the server restarts).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from aibroker.brokers.base import OrderIntent
from aibroker.config.schema import AppConfig
from aibroker.risk.gate import evaluate_intent
from aibroker.state.runtime import RuntimeState

# זרם דמו מתמשך (תהליך שרת יחיד)
_live_history: list[dict[str, Any]] = []
_live_state: RuntimeState | None = None
_live_profile: str | None = None
_stream_seq: int = 0


def _format_utc_ts(offset_ms: int = 0) -> str:
    """חותמת זמן מקומית עם מילישניות."""
    t = datetime.now() + timedelta(milliseconds=offset_ms)
    ms = t.microsecond // 1000
    return t.strftime("%H:%M:%S") + f".{ms:03d}"


def _session_block(cfg: AppConfig, sid: str) -> dict[str, Any]:
    return {
        "session_id": sid,
        "environment": "DEMO_ONLY",
        "label_he": "חוויית מסחר מלאה — רק דמו; אפס כסף אמיתי",
        "real_components": [
            "שרת HTTP (FastAPI) אצלך על המחשב",
            "שער ריסק evaluate_intent — אותו קוד כמו לפני ביצוע אמיתי",
            "פרופיל YAML + AppConfig (Pydantic)",
        ],
        "simulated_only": [
            "שליחת הוראות ל-IBKR / ברוקר",
            "כסף, מילוי, PnL בחשבון אמיתי",
            "כאן הכוונות בדמו מסקריפט/זרם — לא ממנוע אסטרטגיה חי (בשלב זה)",
        ],
    }


def _build_row(
    cfg: AppConfig,
    state: RuntimeState,
    *,
    side: str,
    sym: str,
    qty: float,
    ref_px: float,
    strategy_source: str,
    strategy_note: str,
    time_offset_ms: int = 0,
    trade_seq: int = 0,
) -> dict[str, Any]:
    allowed_list = [s.upper() for s in (cfg.risk.allowed_symbols or [])]
    ts = _format_utc_ts(time_offset_ms)
    est = round(qty * ref_px, 2)
    trades_before = state.trades_today
    intent = OrderIntent(symbol=sym, side=side, quantity=qty, order_type="market")
    d = evaluate_intent(cfg, state, intent, estimated_notional_usd=qty * ref_px)

    sym_u = sym.upper()
    in_whitelist = (not allowed_list) or (sym_u in allowed_list)
    notional_ok = est <= cfg.risk.max_notional_per_trade_usd

    row: dict[str, Any] = {
        "seq": trade_seq,
        "time": ts,
        "symbol": sym_u,
        "side": side.upper(),
        "qty": qty,
        "ref_price_usd": round(ref_px, 2),
        "risk_ok": d.allowed,
        "risk_reason": d.reason,
        "analysis": {
            "intent_summary": f"{side.upper()} {qty} {sym_u} · נוטיונל משוער ~${est:,.2f}",
            "strategy": {
                "mode": cfg.strategy.mode,
                "source": strategy_source,
                "note": strategy_note,
            },
            "risk": {
                "gate": "evaluate_intent",
                "estimated_notional_usd": est,
                "max_notional_per_trade_usd": cfg.risk.max_notional_per_trade_usd,
                "notional_ok": notional_ok,
                "whitelist": allowed_list,
                "symbol_allowed_by_list": in_whitelist,
                "max_trades_per_day": cfg.risk.max_trades_per_day,
                "trades_today_before": trades_before,
                "decision_allowed": d.allowed,
                "decision_reason": d.reason,
            },
            "execution": {
                "mode": "dry_run" if d.allowed else "blocked",
                "broker": cfg.broker,
                "account_mode": cfg.account_mode,
            },
            "pipeline": [
                "נתונים (מחיר ייחוס מהדמו / Alpha Vantage אם מוגדר)",
                "אסטרטגיה (כאן: דמו / זרם)",
                "שער ריסק (evaluate_intent)",
                "ביצוע (רישום dry_run בלבד)",
            ],
        },
    }

    if d.allowed:
        row["execution"] = "DRY_RUN_RECORDED"
        row["message"] = (
            f"[dry_run] רישום בלבד — לא נשלח לברוקר. הייתי שולח {side.upper()} {qty} {sym.upper()} @~${ref_px:.2f}"
        )
        state.trades_today += 1
    else:
        row["execution"] = "BLOCKED_BY_RISK"
        row["message"] = f"חסום: {d.reason}"

    return row


def _reset_live(cfg: AppConfig) -> RuntimeState:
    global _live_history, _live_state, _live_profile, _stream_seq
    _live_history = []
    _stream_seq = 0
    _live_profile = cfg.profile_name
    _live_state = RuntimeState(
        profile_name=cfg.profile_name,
        account_mode=cfg.account_mode,
        dry_run=cfg.execution.dry_run,
        kill_switch=cfg.risk.kill_switch,
        trades_today=0,
        daily_pnl_usd=0.0,
    )
    return _live_state


def _response_payload(
    cfg: AppConfig,
    trades: list[dict[str, Any]],
    log_lines: list[str],
    *,
    sid: str,
) -> dict[str, Any]:
    return {
        "disclaimer": "דמו בלבד — אין כסף אמיתי, אין שליחה ל-IBKR. ה-API והשער ריסק אמיתיים בקוד.",
        "profile": cfg.profile_name,
        "dry_run": cfg.execution.dry_run,
        "trades": trades,
        "log_lines": log_lines,
        "summary": {
            "total_intents": len(trades),
            "filled_dry_run": sum(1 for t in trades if t.get("execution") == "DRY_RUN_RECORDED"),
            "blocked": sum(1 for t in trades if t.get("execution") == "BLOCKED_BY_RISK"),
        },
        "session": _session_block(cfg, sid),
        "live_stream": {
            "enabled": True,
            "interval_hint_sec": 6,
            "append_endpoint": "/api/simulation/trade-tick",
            "note": "עסקאות נוספות נצברות בזיכרון השרת עד רענון/הפעלה מחדש",
        },
    }


def run_trade_demo_session(cfg: AppConfig) -> dict[str, Any]:
    from aibroker.web.demo_data import build_demo_charts

    demo = build_demo_charts(cfg)
    last_px = float(demo["ohlc"][-1]["c"]) if demo.get("ohlc") else 450.0
    symbols = cfg.risk.allowed_symbols or ["SPY", "QQQ"]
    state = _reset_live(cfg)

    raw_intents: list[tuple[str, str, float, float]] = [
        ("buy", symbols[0], 2.0, last_px * 0.999),
        ("sell", symbols[0], 1.0, last_px * 1.001),
        ("buy", symbols[-1] if len(symbols) > 1 else symbols[0], 1.0, last_px * 0.998),
        ("buy", "AAPL", 5.0, 180.0),
    ]

    trades: list[dict[str, Any]] = []
    log_lines: list[str] = []
    note = (
        "הכוונות בדמו מסקריפט קבוע בקוד — לא מחישוב אסטרטגיה חיה (שלב עתידי: סיגנלים מ־SimpleRules וכו׳)."
    )

    for i, (side, sym, qty, ref_px) in enumerate(raw_intents):
        row = _build_row(
            cfg,
            state,
            side=side,
            sym=sym,
            qty=qty,
            ref_px=ref_px,
            strategy_source="demo_script",
            strategy_note=note,
            time_offset_ms=i * 120,
            trade_seq=i + 1,
        )
        trades.append(row)
        _live_history.append(row)
        if row.get("execution") == "DRY_RUN_RECORDED":
            log_lines.append(row["message"])
        else:
            log_lines.append(f"חסום {sym}: {row['risk_reason']}")

    sid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    return _response_payload(cfg, trades, log_lines, sid=sid)


def append_demo_trade_tick(cfg: AppConfig) -> dict[str, Any]:
    """מוסיף עסקת דמו אחת לזרם (אותו מצב ריצה כמו אחרי run_trade_demo_session)."""
    from aibroker.web.demo_data import build_demo_charts

    global _live_state, _live_history, _stream_seq

    if _live_state is None or _live_profile != cfg.profile_name:
        return run_trade_demo_session(cfg)

    demo = build_demo_charts(cfg)
    last_px = float(demo["ohlc"][-1]["c"]) if demo.get("ohlc") else 450.0
    symbols = cfg.risk.allowed_symbols or ["SPY", "QQQ"]

    _stream_seq += 1
    n = _stream_seq
    side = "buy" if n % 2 == 1 else "sell"
    sym = symbols[n % len(symbols)]
    if n % 9 == 0:
        sym = "AAPL"
    qty = float(1 + (n % 4))
    jitter = 0.992 + (n % 17) * 0.001
    ref_px = last_px * jitter

    note = (
        f"עסקה #{n} בזרם הדמו — נשלחת מחדש כל כמה שניות מהדפדפן; "
        "אותו שער ריסק, ללא כסף אמיתי."
    )
    next_seq = len(_live_history) + 1
    row = _build_row(
        cfg,
        _live_state,
        side=side,
        sym=sym,
        qty=qty,
        ref_px=ref_px,
        strategy_source="demo_stream",
        strategy_note=note,
        time_offset_ms=0,
        trade_seq=next_seq,
    )
    _live_history.append(row)
    if len(_live_history) > 200:
        del _live_history[:-200]

    sid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    log_lines = [row["message"]] if row.get("message") else []
    return _response_payload(cfg, list(_live_history), log_lines, sid=sid)
