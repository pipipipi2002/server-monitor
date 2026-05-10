"""Pick the right per-OS collector at call time."""

from __future__ import annotations

import sys

from server_monitor_agent.snapshot import Session


def _collect_linux() -> list[Session]:
    from server_monitor_agent.collect_linux import collect as _c

    return _c()


def _collect_windows() -> list[Session]:
    from server_monitor_agent.collect_windows import collect as _c

    return _c()


def collect() -> list[Session]:
    if sys.platform == "win32":
        return _collect_windows()
    return _collect_linux()
