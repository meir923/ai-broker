import json
import logging
import random
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "aibroker.db"

_conn: sqlite3.Connection | None = None
_db_lock = threading.Lock()


def _sqlite_retry_write(op: Callable[[], None], *, attempts: int = 8) -> None:
    """Retry DB writes on transient SQLITE_BUSY / database locked."""
    delay = 0.02
    last: sqlite3.OperationalError | None = None
    for i in range(attempts):
        try:
            op()
            return
        except sqlite3.OperationalError as e:
            last = e
            msg = str(e).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            if i == attempts - 1:
                raise
            time.sleep(delay + random.uniform(0, 0.02))
            delay = min(delay * 2, 0.6)
    if last:
        raise last


def ensure_data_dirs() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _get_db() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    ensure_data_dirs()
    _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            mode TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            deposit REAL NOT NULL,
            symbols TEXT NOT NULL,
            final_equity REAL,
            final_pnl REAL,
            final_pnl_pct REAL,
            total_steps INTEGER,
            total_trades INTEGER,
            pnl_peak REAL,
            pnl_trough REAL,
            ended_at TEXT
        )
    """)
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            step INTEGER,
            date TEXT,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            price REAL,
            qty REAL,
            reason TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            step INTEGER,
            date TEXT,
            data TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            session_id INTEGER,
            mode TEXT,
            risk_level TEXT,
            deposit REAL,
            symbols TEXT,
            cash REAL,
            step INTEGER,
            running INTEGER DEFAULT 0,
            positions_json TEXT,
            equity_peak REAL,
            equity_trough REAL,
            updated_at TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)
    _conn.commit()
    return _conn


def save_session_start(mode: str, risk_level: str, deposit: float, symbols: list[str]) -> int:
    out: list[int] = []

    def op() -> None:
        db = _get_db()
        cur = db.execute(
            "INSERT INTO sessions (started_at, mode, risk_level, deposit, symbols) VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), mode, risk_level, deposit, ",".join(symbols)),
        )
        db.commit()
        out.append(cur.lastrowid or 0)

    with _db_lock:
        _sqlite_retry_write(op)
    return out[0] if out else 0


def save_session_end(session_id: int, equity: float, pnl: float, pnl_pct: float,
                     steps: int, trades: int, pnl_peak: float, pnl_trough: float) -> None:
    def op() -> None:
        db = _get_db()
        db.execute(
            "UPDATE sessions SET final_equity=?, final_pnl=?, final_pnl_pct=?, total_steps=?, "
            "total_trades=?, pnl_peak=?, pnl_trough=?, ended_at=? WHERE id=?",
            (equity, pnl, pnl_pct, steps, trades, pnl_peak, pnl_trough,
             datetime.now(timezone.utc).isoformat(), session_id),
        )
        db.commit()

    with _db_lock:
        _sqlite_retry_write(op)


def save_trades(session_id: int, trades: list[dict[str, Any]]) -> None:
    def op() -> None:
        db = _get_db()
        for t in trades:
            db.execute(
                "INSERT INTO trades (session_id, step, date, symbol, action, price, qty, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, t.get("step"), t.get("date"), t.get("symbol", ""),
                 t.get("action", ""), t.get("price", 0), t.get("qty", 0), t.get("reason", "")),
            )
        db.commit()

    with _db_lock:
        _sqlite_retry_write(op)


def save_decisions(session_id: int, decisions: list[dict[str, Any]]) -> None:
    def op() -> None:
        db = _get_db()
        for d in decisions:
            db.execute(
                "INSERT INTO decisions (session_id, step, date, data) VALUES (?, ?, ?, ?)",
                (session_id, d.get("step"), d.get("date"), json.dumps(d, ensure_ascii=False)),
            )
        db.commit()

    with _db_lock:
        _sqlite_retry_write(op)


def get_session_history(limit: int = 20) -> list[dict[str, Any]]:
    db = _get_db()
    rows = db.execute(
        "SELECT id, started_at, mode, risk_level, deposit, symbols, "
        "final_equity, final_pnl, final_pnl_pct, total_steps, total_trades, "
        "pnl_peak, pnl_trough, ended_at FROM sessions ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    cols = ["id", "started_at", "mode", "risk_level", "deposit", "symbols",
            "final_equity", "final_pnl", "final_pnl_pct", "total_steps", "total_trades",
            "pnl_peak", "pnl_trough", "ended_at"]
    return [dict(zip(cols, row)) for row in rows]


def get_session_trades(session_id: int) -> list[dict[str, Any]]:
    db = _get_db()
    rows = db.execute(
        "SELECT step, date, symbol, action, price, qty, reason FROM trades WHERE session_id=? ORDER BY id",
        (session_id,),
    ).fetchall()
    cols = ["step", "date", "symbol", "action", "price", "qty", "reason"]
    return [dict(zip(cols, row)) for row in rows]


def save_agent_state(session_id: int, mode: str, risk_level: str, deposit: float,
                     symbols: list[str], cash: float, step: int, running: bool,
                     positions: dict[str, dict], equity_peak: float, equity_trough: float) -> None:
    def op() -> None:
        db = _get_db()
        db.execute("DELETE FROM agent_state")
        db.execute(
            "INSERT INTO agent_state (id, session_id, mode, risk_level, deposit, symbols, cash, step, "
            "running, positions_json, equity_peak, equity_trough, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, mode, risk_level, deposit, ",".join(symbols), cash, step,
             1 if running else 0, json.dumps(positions, ensure_ascii=False),
             equity_peak, equity_trough, datetime.now(timezone.utc).isoformat()),
        )
        db.commit()

    with _db_lock:
        _sqlite_retry_write(op)


def load_agent_state() -> dict[str, Any] | None:
    db = _get_db()
    row = db.execute(
        "SELECT session_id, mode, risk_level, deposit, symbols, cash, step, running, "
        "positions_json, equity_peak, equity_trough, updated_at FROM agent_state WHERE id=1"
    ).fetchone()
    if not row:
        return None
    cols = ["session_id", "mode", "risk_level", "deposit", "symbols", "cash", "step",
            "running", "positions_json", "equity_peak", "equity_trough", "updated_at"]
    d = dict(zip(cols, row))
    d["symbols"] = d["symbols"].split(",") if d["symbols"] else []
    d["positions"] = json.loads(d["positions_json"]) if d["positions_json"] else {}
    d["running"] = bool(d["running"])
    return d


def clear_agent_state() -> None:
    def op() -> None:
        db = _get_db()
        db.execute("DELETE FROM agent_state")
        db.commit()

    with _db_lock:
        _sqlite_retry_write(op)
