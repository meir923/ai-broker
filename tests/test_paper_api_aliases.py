"""Paper API primary + fallback paths (same app)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aibroker.web.server import create_app

PROFILE = Path(__file__).resolve().parents[1] / "config" / "profiles" / "paper_safe.yaml"


def test_paper_status_all_get_aliases() -> None:
    app = create_app(PROFILE, port=8765, open_browser=False)
    with TestClient(app) as client:
        for path in ("/api/paper/status", "/api/simulation/paper/status", "/api/paper_status"):
            r = client.get(path)
            assert r.status_code == 200, path
            data = r.json()
            assert data.get("ok") is True
            assert "running" in data


def test_paper_start_stop_simulation_paths() -> None:
    app = create_app(PROFILE, port=8765, open_browser=False)
    with TestClient(app) as client:
        r = client.post(
            "/api/simulation/paper/start",
            json={"deposit_usd": 5000.0, "interval_sec": 10.0},
        )
        assert r.status_code == 200
        assert r.json().get("ok") is True
        r2 = client.post("/api/simulation/paper/stop")
        assert r2.status_code == 200
        assert r2.json().get("running") is False
