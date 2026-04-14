from __future__ import annotations

from pathlib import Path

import pytest

from aibroker.config.loader import load_profile
from aibroker.simulation import paper_autopilot as pa

PROFILES = Path(__file__).resolve().parents[1] / "config" / "profiles"
PAPER = PROFILES / "paper_safe.yaml"


@pytest.fixture(autouse=True)
def _reset_paper() -> None:
    pa.configure_paper_autopilot(PAPER)
    pa.paper_stop()
    pa._portfolio_mgr = None
    yield
    pa.paper_stop()
    pa._portfolio_mgr = None


def test_paper_start_status_cash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    cfg = load_profile(PAPER)
    out = pa.paper_start(cfg, deposit_usd=25_000.0, interval_sec=10.0)
    assert out["ok"] is True
    assert out["active"] is True
    assert out["running"] is True
    assert out["cash_usd"] == 25_000.0
    st = pa.paper_status(cfg)
    assert st["initial_deposit_usd"] == 25_000.0


def test_paper_step_advances_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    cfg = load_profile(PAPER)
    pa.paper_start(cfg, deposit_usd=100_000.0, interval_sec=5.0)
    for _ in range(100):
        pa.paper_step_once_for_test(cfg)
    st = pa.paper_status(cfg)
    assert st["days_simulated"] >= 50
    assert st["tick_count"] >= 50


def test_paper_rejects_non_finite_deposit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    cfg = load_profile(PAPER)
    out = pa.paper_start(cfg, deposit_usd=float("nan"), interval_sec=18.0)
    assert out["ok"] is False


def test_paper_step_with_gbm_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulation works even when yfinance is unavailable (GBM fallback)."""
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    import aibroker.data.historical as hist
    monkeypatch.setattr(hist, "_download_yahoo_direct", lambda *a, **kw: None)
    monkeypatch.setattr(hist, "_download_yfinance", lambda *a, **kw: None)
    cfg = load_profile(PAPER)
    pa.paper_start(cfg, deposit_usd=50_000.0, interval_sec=5.0)
    for _ in range(8):
        pa.paper_step_once_for_test(cfg)
    st = pa.paper_status(cfg)
    assert st["active"] is True
    assert st["days_simulated"] >= 1


def test_paper_stop_keeps_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    cfg = load_profile(PAPER)
    pa.paper_start(cfg, deposit_usd=10_000.0, interval_sec=30.0)
    pa.paper_stop()
    st = pa.paper_status(cfg)
    assert st["running"] is False
    assert st["active"] is True


def test_paper_status_has_swing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    cfg = load_profile(PAPER)
    pa.paper_start(cfg, deposit_usd=100_000.0, interval_sec=5.0)
    st = pa.paper_status(cfg)
    assert "sim_date" in st
    assert "days_simulated" in st
    assert "win_rate" in st
    assert "avg_r" in st
    assert "total_closed" in st
