from __future__ import annotations

from pathlib import Path

from aibroker.config.loader import load_profile
from aibroker.web.demo_data import build_demo_charts

PROFILES = Path(__file__).resolve().parents[1] / "config" / "profiles"
PAPER_SAFE = PROFILES / "paper_safe.yaml"


def test_demo_charts_shape(monkeypatch) -> None:
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    cfg = load_profile(PAPER_SAFE)
    d = build_demo_charts(cfg)
    assert "equity" in d and len(d["equity"]["values"]) == len(d["equity"]["labels"])
    assert len(d["weights"]["labels"]) == len(d["weights"]["values"])
    assert "risk_usage" in d
    assert len(d["ohlc"]) >= 1
