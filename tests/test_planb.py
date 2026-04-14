"""Plan B — engine, API surface, intraday placeholder."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from aibroker.planb.backtest.engine import run_backtest
from aibroker.planb.config import PlanBCostsConfig, PlanBOOSConfig, PlanBRiskConfig
from aibroker.planb.data.us_bars import (
    intraday_rows_to_bars,
    load_us_intraday,
    load_us_intraday_placeholder,
)
from aibroker.planb.strategies.ma_cross import MACrossStrategy
from aibroker.web.server import create_app

PROFILE = Path(__file__).resolve().parents[1] / "config" / "profiles" / "paper_safe.yaml"


def _bars(n: int = 50) -> list[dict]:
    out: list[dict] = []
    base = 100.0
    start = datetime(2023, 1, 3)
    for i in range(n):
        c = base + i * 0.1 + (0.5 if i > 25 else 0.0)
        ds = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append(
            {
                "date": ds,
                "o": c - 0.2,
                "h": c + 0.2,
                "l": c - 0.3,
                "c": c,
                "volume": 1_000_000,
            }
        )
    return out


def test_run_backtest_engine_smoke() -> None:
    strat = MACrossStrategy(fast=3, slow=8)
    costs = PlanBCostsConfig()
    risk = PlanBRiskConfig(
        max_notional_per_trade_usd=50_000.0,
        max_trades_per_day=100,
        max_daily_loss_usd=1_000_000.0,
        kill_switch=False,
        allowed_symbols=["SPY"],
    )
    oos = PlanBOOSConfig(mode="holdout_end", train_fraction=0.7)
    res = run_backtest(
        _bars(45),
        strat,
        symbol="SPY",
        initial_cash_usd=100_000.0,
        costs=costs,
        risk=risk,
        oos=oos,
    )
    assert res.ok is True
    assert res.bars_used == 45
    assert isinstance(res.return_pct_full, (int, float))
    assert "mode" in res.oos


def test_intraday_placeholder_empty() -> None:
    d = load_us_intraday_placeholder(["SPY"])
    assert d == {"SPY": []}


def test_intraday_no_alpaca_prefers_empty() -> None:
    d = load_us_intraday(["SPY"], prefer_alpaca=False)
    assert d == {"SPY": []}


def test_intraday_rows_to_bars() -> None:
    rows = [
        {"t": "2024-01-02T14:30:00+00:00", "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 1000},
        {"t": "2024-01-02T15:30:00+00:00", "o": 1.05, "h": 1.2, "l": 1.0, "c": 1.15, "v": 1100},
    ]
    bars = intraday_rows_to_bars(rows)
    assert len(bars) == 2
    assert bars[0]["date"].startswith("2024-01-02")
    assert bars[0]["c"] == 1.05


@patch("aibroker.planb.data.us_bars.load_us_intraday")
def test_planb_sim_intraday_start(mock_intraday) -> None:
    base = datetime(2024, 1, 2, 14, 0, tzinfo=timezone.utc)
    rows = [
        {
            "t": (base + timedelta(hours=i)).isoformat(),
            "o": 100.0 + i,
            "h": 101.0 + i,
            "l": 99.0 + i,
            "c": 100.5 + i,
            "v": 1000,
        }
        for i in range(40)
    ]
    mock_intraday.return_value = {"SPY": rows}
    app = create_app(PROFILE, port=8765, open_browser=False)
    with TestClient(app) as client:
        r = client.post(
            "/api/planb/sim/start",
            json={
                "symbol": "SPY",
                "bars": 200,
                "strategy_id": "ma_cross",
                "bar_source": "intraday",
                "timeframe_minutes": 60,
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ok") is True
        assert data.get("session", {}).get("bar_source") == "intraday"
        client.post("/api/planb/sim/stop")


def test_planb_api_config_and_kill_switch() -> None:
    app = create_app(PROFILE, port=8765, open_browser=False)
    with TestClient(app) as client:
        r = client.get("/api/planb/config")
        assert r.status_code == 200
        assert r.json().get("ok") is True
        assert "allowed_symbols" in r.json()

        r2 = client.post("/api/planb/risk/kill-switch", json={"active": True})
        assert r2.status_code == 200
        assert r2.json().get("runtime_kill_switch") is True

        r3 = client.post("/api/planb/risk/kill-switch", json={"active": False})
        assert r3.json().get("runtime_kill_switch") is False


@patch("aibroker.planb.data.us_bars.load_us_daily_bars")
def test_planb_backtest_run_endpoint(mock_load) -> None:
    mock_load.return_value = {"SPY": _bars(60)}
    app = create_app(PROFILE, port=8765, open_browser=False)
    with TestClient(app) as client:
        r = client.post(
            "/api/planb/backtest/run",
            json={
                "symbol": "SPY",
                "bars": 60,
                "strategy_id": "ma_cross",
                "strategy_params": {"fast": 3, "slow": 8},
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ok") is True
        assert data.get("symbol") == "SPY"
        assert isinstance(data.get("trades"), list)


@patch("aibroker.planb.data.us_bars.load_us_daily_bars")
def test_planb_sim_lifecycle(mock_load) -> None:
    mock_load.return_value = {"SPY": _bars(60)}
    app = create_app(PROFILE, port=8765, open_browser=False)
    with TestClient(app) as client:
        r = client.post(
            "/api/planb/sim/start",
            json={"symbol": "SPY", "bars": 60, "strategy_id": "ma_cross"},
        )
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True
        r2 = client.post("/api/planb/sim/step", json={"use_llm": False})
        assert r2.status_code == 200
        assert r2.json().get("ok") is True
        st = client.get("/api/planb/sim/status")
        assert st.json().get("session") is not None
        client.post("/api/planb/sim/stop")


def test_planb_live_status() -> None:
    app = create_app(PROFILE, port=8765, open_browser=False)
    with TestClient(app) as client:
        r = client.get("/api/planb/live/status")
        assert r.status_code == 200
        body = r.json()
        assert "allowed" in body
