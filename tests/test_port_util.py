from __future__ import annotations

import socket

from aibroker.web.port_util import pick_dashboard_port


def test_pick_dashboard_port_returns_usable_port() -> None:
    p = pick_dashboard_port("127.0.0.1", start=38441, span=3)
    assert 38441 <= p <= 38443
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", p))
    finally:
        s.close()
