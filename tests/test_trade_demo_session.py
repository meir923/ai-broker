from __future__ import annotations

from pathlib import Path

import pytest

from aibroker.config.loader import load_profile
from aibroker.simulation.demo_trades import run_trade_demo_session

PROFILES = Path(__file__).resolve().parents[1] / "config" / "profiles"
PAPER = PROFILES / "paper_safe.yaml"


def test_trade_demo_returns_session_and_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    cfg = load_profile(PAPER)
    out = run_trade_demo_session(cfg)
    assert "session" in out
    assert out["session"]["environment"] == "DEMO_ONLY"
    assert out["session"]["session_id"]
    assert out["trades"][0]["analysis"]["strategy"]["mode"] == cfg.strategy.mode


def test_demo_stream_appends_trades(monkeypatch: pytest.MonkeyPatch) -> None:
    from aibroker.simulation.demo_trades import append_demo_trade_tick

    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    cfg = load_profile(PAPER)
    first = run_trade_demo_session(cfg)
    assert len(first["trades"]) == 4
    second = append_demo_trade_tick(cfg)
    assert len(second["trades"]) == 5
    third = append_demo_trade_tick(cfg)
    assert len(third["trades"]) == 6
