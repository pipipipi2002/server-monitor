"""Parse fixtures emulating `who -u` and `loginctl list-sessions` output."""

from __future__ import annotations


WHO_FIXTURE = (
    "alice    pts/0        2030-01-01 11:55 (192.168.1.42)\n"
    "alice    tty1         2030-01-01 09:00\n"
)


def test_parse_who_extracts_remote_ssh_session() -> None:
    from server_monitor_agent.collect_linux import parse_who

    sessions = parse_who(WHO_FIXTURE)
    devices = {s["device_name"] for s in sessions}
    assert "192.168.1.42" in devices

    s = next(s for s in sessions if s["device_name"] == "192.168.1.42")
    assert s["protocol"] == "ssh"
    assert s["state"] == "active"
    assert s["username"] == "alice"
    assert s["logon_at"].startswith("2030-01-01T11:55")


def test_parse_who_marks_console_session() -> None:
    from server_monitor_agent.collect_linux import parse_who

    sessions = parse_who(WHO_FIXTURE)
    s = next(s for s in sessions if s["protocol"] == "console")
    assert s["device_name"] == "tty1"
    assert s["state"] == "active"


def test_parse_who_handles_empty_input() -> None:
    from server_monitor_agent.collect_linux import parse_who

    assert parse_who("") == []
