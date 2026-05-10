"""The Windows collector wraps WTS APIs; tests mock the pywin32 surface.

Verified end-to-end manually on a real Windows host (see Phase 8).
"""

from __future__ import annotations

import sys
import types

import pytest


class _FakeWin32ts:
    WTS_CURRENT_SERVER_HANDLE = 0
    WTSActive = 0
    WTSDisconnected = 4
    WTSConnectState = 8
    WTSClientName = 10
    WTSUserName = 5

    def __init__(self, sessions: list[dict]) -> None:
        self._sessions = sessions

    def WTSEnumerateSessions(self, _h):  # noqa: N802
        return [(s["id"], s.get("name", "rdp-tcp"), s.get("state", self.WTSActive)) for s in self._sessions]

    def WTSQuerySessionInformation(self, _h, sid, code):  # noqa: N802
        for s in self._sessions:
            if s["id"] == sid:
                if code == self.WTSClientName:
                    return s.get("client_name", "")
                if code == self.WTSUserName:
                    return s.get("user", "")
                if code == self.WTSConnectState:
                    return s.get("state", self.WTSActive)
        return ""


@pytest.fixture
def fake_wts(monkeypatch: pytest.MonkeyPatch):
    def install(sessions: list[dict]) -> _FakeWin32ts:
        fake = _FakeWin32ts(sessions)
        mod = types.ModuleType("win32ts")
        for attr in (
            "WTS_CURRENT_SERVER_HANDLE", "WTSActive", "WTSDisconnected",
            "WTSConnectState", "WTSClientName", "WTSUserName",
        ):
            setattr(mod, attr, getattr(fake, attr))
        mod.WTSEnumerateSessions = fake.WTSEnumerateSessions
        mod.WTSQuerySessionInformation = fake.WTSQuerySessionInformation
        monkeypatch.setitem(sys.modules, "win32ts", mod)
        return fake
    return install


def test_collect_returns_active_rdp_session_with_client_name(fake_wts) -> None:
    fake_wts([{"id": 1, "state": 0, "client_name": "LAPTOP-A", "user": "shared"}])

    from server_monitor_agent.collect_windows import collect

    out = collect()
    assert len(out) == 1
    s = out[0]
    assert s["device_name"] == "LAPTOP-A"
    assert s["state"] == "active"
    assert s["protocol"] == "rdp"
    assert s["username"] == "shared"


def test_collect_skips_session_without_client_name(fake_wts) -> None:
    """Session 0 (the services session) has no client name; ignore it."""
    fake_wts([{"id": 0, "state": 0, "client_name": "", "user": "SYSTEM"}])

    from server_monitor_agent.collect_windows import collect

    assert collect() == []


def test_collect_marks_disconnected_state(fake_wts) -> None:
    fake_wts([{"id": 2, "state": 4, "client_name": "LAPTOP-B", "user": "shared"}])

    from server_monitor_agent.collect_windows import collect

    out = collect()
    assert out and out[0]["state"] == "disconnected"
