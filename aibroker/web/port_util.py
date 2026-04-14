"""Pick a free TCP port for the local dashboard (avoid stale process on 8765)."""

from __future__ import annotations

import socket


def pick_dashboard_port(
    host: str = "127.0.0.1",
    *,
    start: int = 8765,
    span: int = 60,
) -> int:
    bind_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    last_err: OSError | None = None
    for port in range(start, start + span):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((bind_host, port))
            return port
        except OSError as e:
            last_err = e
        finally:
            s.close()
    msg = f"אין פורט פנוי בטווח {start}–{start + span - 1} על {bind_host}"
    raise OSError(msg) from last_err
