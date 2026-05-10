import os
import sys
from pathlib import Path


def test_save_and_load_round_trip(token_file: Path) -> None:
    from server_monitor_agent.token_store import load_token, save_token

    save_token(token_file, "abc-123")
    assert load_token(token_file) == "abc-123"


def test_save_uses_restrictive_mode_on_posix(token_file: Path) -> None:
    if sys.platform == "win32":
        return
    from server_monitor_agent.token_store import save_token

    save_token(token_file, "x")
    mode = os.stat(token_file).st_mode & 0o777
    assert mode == 0o600


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    from server_monitor_agent.token_store import load_token

    assert load_token(tmp_path / "no-such") is None


def test_default_path_per_os() -> None:
    from server_monitor_agent.token_store import default_token_path

    p = default_token_path()
    if sys.platform == "win32":
        assert "server-monitor-agent" in str(p)
    else:
        assert str(p) == "/etc/server-monitor-agent/token"
