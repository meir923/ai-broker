"""Playwright browser tests against the real uvicorn app."""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="pip install -e '.[e2e]' && playwright install chromium")
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.e2e


def test_dashboard_renders_hebrew_nav(e2e_server_url: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{e2e_server_url}/", wait_until="domcontentloaded")
        assert page.get_by_role("tab", name="סימולציה").is_visible()
        assert page.get_by_role("tab", name="גרפים").is_visible()
        assert page.get_by_role("tab", name="הגדרות").is_visible()
        browser.close()


def test_api_status_ok(e2e_server_url: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        res = page.request.get(f"{e2e_server_url}/api/status")
        assert res.ok
        data = res.json()
        assert data.get("profile_name") == "paper_safe"
        browser.close()


def test_paper_autopilot_ui(e2e_server_url: str) -> None:
    """Paper autopilot: start button and KPI display."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{e2e_server_url}/", wait_until="domcontentloaded")
        assert page.get_by_role("tab", name="סימולציה").get_attribute("aria-selected") == "true"
        assert page.locator("#btn-paper-start").is_visible()
        assert page.locator("#kpi-status").is_visible()
        browser.close()
