"""A2 — exhaustive tests for aibroker/data/storage.py"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

import aibroker.data.storage as st


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path, monkeypatch):
    """Each test gets its own fresh SQLite database."""
    monkeypatch.setattr(st, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(st, "DATA_DIR", tmp_path)
    monkeypatch.setattr(st, "CACHE_DIR", tmp_path / "cache")
    st._conn = None  # force re-init
    yield
    if st._conn is not None:
        st._conn.close()
        st._conn = None


# ── _sqlite_retry_write ──────────────────────────────────────────────────

class TestSqliteRetryWrite:
    def test_success_on_first_try(self):
        called = [0]
        def op():
            called[0] += 1
        st._sqlite_retry_write(op)
        assert called[0] == 1

    def test_retries_on_locked(self):
        attempt = [0]
        def op():
            attempt[0] += 1
            if attempt[0] < 3:
                raise sqlite3.OperationalError("database is locked")
        st._sqlite_retry_write(op, attempts=5)
        assert attempt[0] == 3

    def test_raises_after_max_attempts(self):
        def op():
            raise sqlite3.OperationalError("database is locked")
        with pytest.raises(sqlite3.OperationalError):
            st._sqlite_retry_write(op, attempts=3)

    def test_non_locked_error_raises_immediately(self):
        def op():
            raise sqlite3.OperationalError("no such table")
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            st._sqlite_retry_write(op, attempts=5)


# ── _get_db schema ───────────────────────────────────────────────────────

class TestGetDb:
    def test_creates_all_tables(self):
        db = st._get_db()
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert {"sessions", "trades", "decisions", "agent_state"} <= tables

    def test_singleton(self):
        db1 = st._get_db()
        db2 = st._get_db()
        assert db1 is db2

    def test_wal_mode(self):
        db = st._get_db()
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_foreign_keys_on(self):
        db = st._get_db()
        fk = db.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1


# ── save_session_start / save_session_end ────────────────────────────────

class TestSessionLifecycle:
    def test_start_returns_positive_id(self):
        sid = st.save_session_start("sim", "medium", 100000.0, ["SPY", "AAPL"])
        assert sid > 0

    def test_start_stores_correct_data(self):
        sid = st.save_session_start("paper", "high", 50000.0, ["TSLA"])
        rows = st._get_db().execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row[2] == "paper"  # mode
        assert row[3] == "high"   # risk_level
        assert row[4] == 50000.0  # deposit
        assert row[5] == "TSLA"   # symbols

    def test_end_updates_session(self):
        sid = st.save_session_start("sim", "low", 100000.0, ["SPY"])
        st.save_session_end(sid, 110000.0, 10000.0, 10.0, 50, 20, 12000.0, -3000.0)
        row = st._get_db().execute("SELECT final_equity, final_pnl FROM sessions WHERE id=?", (sid,)).fetchone()
        assert row[0] == 110000.0
        assert row[1] == 10000.0

    def test_multiple_sessions(self):
        s1 = st.save_session_start("sim", "low", 100000.0, ["A"])
        s2 = st.save_session_start("paper", "high", 50000.0, ["B"])
        assert s2 > s1


# ── save_trades / get_session_trades ─────────────────────────────────────

class TestTrades:
    def test_save_and_retrieve(self):
        sid = st.save_session_start("sim", "medium", 100000.0, ["SPY"])
        trades = [
            {"step": 1, "date": "2024-01-02", "symbol": "SPY", "action": "buy", "price": 500.0, "qty": 10, "reason": "test"},
            {"step": 2, "date": "2024-01-03", "symbol": "SPY", "action": "sell", "price": 510.0, "qty": 10, "reason": "tp"},
        ]
        st.save_trades(sid, trades)
        loaded = st.get_session_trades(sid)
        assert len(loaded) == 2
        assert loaded[0]["symbol"] == "SPY"
        assert loaded[1]["price"] == 510.0

    def test_empty_trades_list(self):
        sid = st.save_session_start("sim", "medium", 100000.0, ["SPY"])
        st.save_trades(sid, [])
        assert st.get_session_trades(sid) == []

    def test_fractional_qty(self):
        sid = st.save_session_start("sim", "medium", 100000.0, ["AAPL"])
        st.save_trades(sid, [{"step": 1, "date": "2024-01-02", "symbol": "AAPL",
                              "action": "buy", "price": 200.0, "qty": 0.5, "reason": "frac"}])
        loaded = st.get_session_trades(sid)
        assert loaded[0]["qty"] == pytest.approx(0.5)

    def test_zero_price_trade(self):
        sid = st.save_session_start("sim", "medium", 100000.0, ["X"])
        st.save_trades(sid, [{"step": 0, "symbol": "X", "action": "buy", "price": 0, "qty": 0}])
        loaded = st.get_session_trades(sid)
        assert loaded[0]["price"] == 0

    def test_trades_isolated_by_session(self):
        s1 = st.save_session_start("sim", "low", 100000.0, ["A"])
        s2 = st.save_session_start("sim", "low", 100000.0, ["B"])
        st.save_trades(s1, [{"step": 1, "symbol": "A", "action": "buy", "price": 10, "qty": 1}])
        st.save_trades(s2, [{"step": 1, "symbol": "B", "action": "sell", "price": 20, "qty": 2}])
        assert len(st.get_session_trades(s1)) == 1
        assert st.get_session_trades(s1)[0]["symbol"] == "A"
        assert st.get_session_trades(s2)[0]["symbol"] == "B"


# ── save_decisions ───────────────────────────────────────────────────────

class TestDecisions:
    def test_save_and_count(self):
        sid = st.save_session_start("sim", "medium", 100000.0, ["SPY"])
        decs = [
            {"step": 1, "date": "2024-01-02", "market_view": "bullish"},
            {"step": 2, "date": "2024-01-03", "market_view": "bearish"},
        ]
        st.save_decisions(sid, decs)
        rows = st._get_db().execute("SELECT COUNT(*) FROM decisions WHERE session_id=?", (sid,)).fetchone()
        assert rows[0] == 2

    def test_decision_data_is_json(self):
        sid = st.save_session_start("sim", "medium", 100000.0, ["SPY"])
        st.save_decisions(sid, [{"step": 1, "date": "2024-01-02", "hebrew": "שורי"}])
        row = st._get_db().execute("SELECT data FROM decisions WHERE session_id=?", (sid,)).fetchone()
        parsed = json.loads(row[0])
        assert parsed["hebrew"] == "שורי"


# ── agent_state ──────────────────────────────────────────────────────────

class TestAgentState:
    def test_save_and_load_round_trip(self):
        sid = st.save_session_start("sim", "medium", 100000.0, ["SPY", "AAPL"])
        positions = {"SPY": {"qty": 10, "avg_cost": 500.0}, "AAPL": {"qty": -5, "avg_cost": 200.0}}
        st.save_agent_state(sid, "sim", "medium", 100000.0, ["SPY", "AAPL"],
                            95000.0, 42, True, positions, 105000.0, 90000.0)
        state = st.load_agent_state()
        assert state is not None
        assert state["session_id"] == sid
        assert state["mode"] == "sim"
        assert state["cash"] == 95000.0
        assert state["step"] == 42
        assert state["running"] is True
        assert state["positions"]["SPY"]["qty"] == 10
        assert state["positions"]["AAPL"]["qty"] == -5
        assert state["symbols"] == ["SPY", "AAPL"]

    def test_overwrite_on_second_save(self):
        sid = st.save_session_start("sim", "low", 100000.0, ["SPY"])
        st.save_agent_state(sid, "sim", "low", 100000.0, ["SPY"], 99000.0, 1, True, {}, 100000.0, 99000.0)
        st.save_agent_state(sid, "sim", "low", 100000.0, ["SPY"], 88000.0, 5, False, {}, 100000.0, 88000.0)
        state = st.load_agent_state()
        assert state["cash"] == 88000.0
        assert state["step"] == 5
        assert state["running"] is False

    def test_load_returns_none_when_empty(self):
        assert st.load_agent_state() is None

    def test_clear_agent_state(self):
        sid = st.save_session_start("sim", "medium", 100000.0, ["SPY"])
        st.save_agent_state(sid, "sim", "medium", 100000.0, ["SPY"], 99000.0, 1, True, {}, 100000.0, 99000.0)
        st.clear_agent_state()
        assert st.load_agent_state() is None

    def test_empty_positions_json(self):
        sid = st.save_session_start("sim", "medium", 100000.0, ["SPY"])
        st.save_agent_state(sid, "sim", "medium", 100000.0, ["SPY"], 100000.0, 0, False, {}, 100000.0, 100000.0)
        state = st.load_agent_state()
        assert state["positions"] == {}

    def test_empty_symbols(self):
        sid = st.save_session_start("sim", "medium", 100000.0, [])
        st.save_agent_state(sid, "sim", "medium", 100000.0, [], 100000.0, 0, False, {}, 100000.0, 100000.0)
        state = st.load_agent_state()
        assert state["symbols"] == []


# ── get_session_history ──────────────────────────────────────────────────

class TestSessionHistory:
    def test_returns_latest_first(self):
        st.save_session_start("sim", "low", 100000.0, ["A"])
        st.save_session_start("sim", "high", 50000.0, ["B"])
        hist = st.get_session_history(limit=10)
        assert len(hist) == 2
        assert hist[0]["symbols"] == "B"
        assert hist[1]["symbols"] == "A"

    def test_limit_respected(self):
        for _ in range(5):
            st.save_session_start("sim", "medium", 100000.0, ["X"])
        hist = st.get_session_history(limit=3)
        assert len(hist) == 3

    def test_empty_db(self):
        assert st.get_session_history() == []


# ── thread safety ────────────────────────────────────────────────────────

class TestThreadSafety:
    def test_concurrent_writes_no_crash(self):
        sid = st.save_session_start("sim", "medium", 100000.0, ["SPY"])
        errors: list[Exception] = []

        def writer(n: int):
            try:
                for i in range(10):
                    st.save_trades(sid, [{"step": n * 100 + i, "symbol": "SPY",
                                         "action": "buy", "price": 100 + i, "qty": 1}])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors == [], f"Concurrent write errors: {errors}"
        total = st._get_db().execute("SELECT COUNT(*) FROM trades WHERE session_id=?", (sid,)).fetchone()[0]
        assert total == 40
