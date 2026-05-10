"""Read/write the agent's long-lived token in an OS-protected file."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def default_token_path() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("ProgramData", r"C:\\ProgramData")
        return Path(base) / "server-monitor-agent" / "token"
    return Path("/etc/server-monitor-agent/token")


def save_token(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write with restrictive mode on POSIX. On Windows we rely on directory ACLs
    # set by the install script (SYSTEM + Administrators only).
    if sys.platform == "win32":
        path.write_text(token, encoding="ascii")
        return
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("ascii"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def load_token(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip() or None
    except FileNotFoundError:
        return None
