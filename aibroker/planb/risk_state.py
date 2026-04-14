"""Process-wide Plan B risk toggles (in addition to YAML `risk.kill_switch`)."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_runtime_kill_switch: bool = False


def set_runtime_kill_switch(active: bool) -> None:
    global _runtime_kill_switch
    with _lock:
        _runtime_kill_switch = bool(active)


def runtime_kill_switch_active() -> bool:
    with _lock:
        return _runtime_kill_switch
