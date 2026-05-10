"""Read login sessions on Linux via `who -u`.

`who -u` lines:
    alice    pts/0        2030-01-01 11:55 (192.168.1.42)   <- remote ssh
    alice    tty1         2030-01-01 09:00                  <- local console

We could also use `loginctl list-sessions --json`, but `who` is universally
available and stable. Keep it simple.
"""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime

from server_monitor_agent.snapshot import Session


# username, line, date, time, optional (remote)
_LINE = re.compile(
    r"^(?P<user>\S+)\s+(?P<line>\S+)\s+(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2})"
    r"(?:\s+\((?P<remote>[^)]+)\))?"
)


def _to_iso(date_part: str, time_part: str) -> str:
    dt = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
    return dt.isoformat()


def parse_who(output: str) -> list[Session]:
    out: list[Session] = []
    for raw in output.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        m = _LINE.match(line)
        if not m:
            continue
        username = m.group("user")
        terminal = m.group("line")
        remote = m.group("remote")
        logon = _to_iso(m.group("date"), m.group("time"))
        if remote:
            out.append({
                "device_name": remote,
                "username": username,
                "protocol": "ssh",
                "state": "active",
                "logon_at": logon,
            })
        else:
            out.append({
                "device_name": terminal,
                "username": username,
                "protocol": "console",
                "state": "active",
                "logon_at": logon,
            })
    return out


def collect() -> list[Session]:
    """Run `who -u` and parse the output. Returns empty on failure."""
    try:
        result = subprocess.run(
            ["who", "-u"], capture_output=True, text=True, timeout=5, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return parse_who(result.stdout)
