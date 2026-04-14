"""K2-K4 — tests for web/port_util"""
from __future__ import annotations

import socket

import pytest

from aibroker.web.port_util import pick_dashboard_port


class TestPickDashboardPort:
    def test_returns_int(self):
        port = pick_dashboard_port(start=49200, span=20)
        assert isinstance(port, int)
        assert 49200 <= port < 49220

    def test_picked_port_is_bindable(self):
        port = pick_dashboard_port(start=49220, span=20)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
        finally:
            s.close()

    def test_span_one(self):
        port = pick_dashboard_port(start=49240, span=1)
        assert port == 49240
