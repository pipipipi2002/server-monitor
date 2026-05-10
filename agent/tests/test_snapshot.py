"""Snapshot diffing — what changed between two consecutive collects."""

from __future__ import annotations


def _s(device: str, state: str = "active") -> dict:
    return {
        "device_name": device,
        "username": "shared",
        "protocol": "rdp",
        "state": state,
        "logon_at": "2030-01-01T11:00:00+00:00",
    }


def test_diff_detects_added_removed_changed() -> None:
    from server_monitor_agent.snapshot import diff_snapshots

    a = [_s("A"), _s("B")]
    b = [_s("A", state="disconnected"), _s("C")]
    d = diff_snapshots(a, b)
    assert [x["device_name"] for x in d.added] == ["C"]
    assert [x["device_name"] for x in d.removed] == ["B"]
    assert [x["device_name"] for x in d.changed] == ["A"]


def test_diff_empty_is_noop() -> None:
    from server_monitor_agent.snapshot import diff_snapshots

    d = diff_snapshots([_s("A")], [_s("A")])
    assert d.added == [] and d.removed == [] and d.changed == []


def test_diff_is_pure() -> None:
    from server_monitor_agent.snapshot import diff_snapshots

    a = [_s("A")]
    b = [_s("B")]
    diff_snapshots(a, b)
    assert a == [_s("A")] and b == [_s("B")]
