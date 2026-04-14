"""Playwright E2E: AIBROKER_E2E=1 plus pip install -e '.[e2e]' and playwright install chromium."""

from __future__ import annotations

import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import uvicorn


@pytest.fixture(scope="session")
def e2e_server_url() -> str:
    if os.environ.get("AIBROKER_E2E") != "1":
        pytest.skip("הגדר AIBROKER_E2E=1 להרצת בדיקות דפדפן Playwright")
    profile = Path(__file__).resolve().parents[2] / "config" / "profiles" / "paper_safe.yaml"
    from aibroker.web.server import create_app

    port = 18765
    app = create_app(profile, port=port, open_browser=False)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{url}/api/status", timeout=0.5)
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    else:
        pytest.fail("שרת E2E לא עלה")

    yield url

    server.should_exit = True
    thread.join(timeout=3)
