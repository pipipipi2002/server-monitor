"""The Windows collector wraps WTS APIs; tests mock the pywin32 surface.

Verified end-to-end manually on a real Windows host (see Phase 8).
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest


class _FakeWin32ts:
    WTS_CURRENT_SERVER_HANDLE = 0
    WTSActive = 0
    WTSDisconnected = 4
    WTSListen = 6
    WTSConnectState = 8
    WTSClientName = 10
    WTSClientAddress = 14
    WTSUserName = 5

    def __init__(self, sessions: list[dict]) -> None:
        self._sessions = sessions

    def WTSEnumerateSessions(self, _h):  # noqa: N802
        return [
            (s["id"], s.get("name", "rdp-tcp"), s.get("state", self.WTSActive))
            for s in self._sessions
        ]

    def WTSQuerySessionInformation(self, _h, sid, code):  # noqa: N802
        for s in self._sessions:
            if s["id"] == sid:
                if code == self.WTSClientName:
                    return s.get("client_name", "")
                if code == self.WTSUserName:
                    return s.get("user", "")
                if code == self.WTSConnectState:
                    return s.get("state", self.WTSActive)
                if code == self.WTSClientAddress:
                    return s.get("client_address")
        return ""


@pytest.fixture
def fake_wts(monkeypatch: pytest.MonkeyPatch):
    def install(sessions: list[dict]) -> _FakeWin32ts:
        fake = _FakeWin32ts(sessions)
        mod = types.ModuleType("win32ts")
        for attr in (
            "WTS_CURRENT_SERVER_HANDLE",
            "WTSActive",
            "WTSDisconnected",
            "WTSListen",
            "WTSConnectState",
            "WTSClientName",
            "WTSClientAddress",
            "WTSUserName",
        ):
            setattr(mod, attr, getattr(fake, attr))
        mod.WTSEnumerateSessions = fake.WTSEnumerateSessions
        mod.WTSQuerySessionInformation = fake.WTSQuerySessionInformation
        monkeypatch.setitem(sys.modules, "win32ts", mod)
        # Force the module under test to be re-imported with these stubs and a
        # fresh module-level _FIRST_SEEN cache between tests.
        sys.modules.pop("server_monitor_agent.collect_windows", None)
        return fake

    return install


def test_collect_returns_active_rdp_session_with_client_name(fake_wts) -> None:
    fake_wts([{"id": 1, "state": 0, "name": "rdp-tcp#0",
               "client_name": "LAPTOP-A", "user": "shared"}])

    from server_monitor_agent.collect_windows import collect

    out = collect()
    assert len(out) == 1
    s = out[0]
    assert s["device_name"] == "LAPTOP-A"
    assert s["state"] == "active"
    assert s["protocol"] == "rdp"
    assert s["username"] == "shared"


def test_collect_skips_services_session(fake_wts) -> None:
    """The Services session has no logged-in user and a non-rdp WinStation name."""
    fake_wts([{"id": 0, "state": 4, "name": "Services",
               "client_name": "", "user": ""}])

    from server_monitor_agent.collect_windows import collect

    assert collect() == []


def test_collect_skips_console_session(fake_wts) -> None:
    """The console session is not RDP — its WinStation name doesn't start with 'rdp-'."""
    fake_wts([{"id": 1, "state": 0, "name": "Console",
               "client_name": "", "user": "Administrator"}])

    from server_monitor_agent.collect_windows import collect

    assert collect() == []


def test_collect_skips_listener(fake_wts) -> None:
    """The rdp-tcp listener pseudo-session must be filtered out."""
    fake_wts([{"id": 65536, "state": _FakeWin32ts.WTSListen, "name": "rdp-tcp",
               "client_name": "", "user": ""}])

    from server_monitor_agent.collect_windows import collect

    assert collect() == []


def test_collect_marks_disconnected_state(fake_wts) -> None:
    fake_wts([{"id": 2, "state": 4, "name": "rdp-tcp#0",
               "client_name": "LAPTOP-B", "user": "shared"}])

    from server_monitor_agent.collect_windows import collect

    out = collect()
    assert out and out[0]["state"] == "disconnected"


def test_collect_falls_back_to_client_address_when_client_name_empty(fake_wts) -> None:
    """Non-Windows RDP clients (Mac/iOS/FreeRDP) often send empty WTSClientName.

    We fall back to the client IP so the session still shows up.
    """
    fake_wts([{
        "id": 2, "state": 0, "name": "rdp-tcp#111",
        "client_name": "",
        "client_address": (2, b"\x64\x00\x02\x0d"),  # AF_INET, 100.0.2.13
        "user": "Administrator",
    }])

    from server_monitor_agent.collect_windows import collect

    out = collect()
    assert len(out) == 1
    assert out[0]["device_name"] == "100.0.2.13"
    assert out[0]["username"] == "Administrator"


def test_collect_falls_back_to_winstation_name_when_no_address(fake_wts) -> None:
    """Last resort when both WTSClientName and WTSClientAddress are empty."""
    fake_wts([{
        "id": 2, "state": 0, "name": "rdp-tcp#111",
        "client_name": "",
        "client_address": None,
        "user": "Administrator",
    }])

    from server_monitor_agent.collect_windows import collect

    out = collect()
    assert len(out) == 1
    assert out[0]["device_name"] == "rdp-tcp#111"
