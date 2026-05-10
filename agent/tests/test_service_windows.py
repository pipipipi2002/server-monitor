"""Smoke tests that the Windows service module imports and exposes the right surface.

We can't actually drive Windows Service Control Manager from a Linux dev box, so
these tests inject fake pywin32 modules into sys.modules before importing
service_windows, exercise the API contracts we depend on (class shape, presence
of enter_service_dispatcher), and stop short of calling StartServiceCtrlDispatcher.
End-to-end SCM behavior is verified manually on a real Windows host.
"""

from __future__ import annotations

import sys
import types

import pytest


def _install_pywin32_stubs(monkeypatch: pytest.MonkeyPatch):
    """Inject fake pywin32 modules, freshly load service_windows against them, return both.

    Returns a tuple (service_windows_module, fakes_dict). Callers should use the
    returned module — `from server_monitor_agent import service_windows` would
    pick up a stale cached version because the parent package keeps a reference
    to the submodule even after we drop it from sys.modules. importlib.reload
    is the only way to re-execute the module body with the new stubs in place.
    """
    import importlib

    fake_w32util = types.ModuleType("win32serviceutil")
    fake_svc = types.ModuleType("win32service")
    fake_event = types.ModuleType("win32event")
    fake_smgr = types.ModuleType("servicemanager")

    class _SvcBase:
        def __init__(self, *_a, **_kw):
            pass

        @classmethod
        def Install(cls, *a, **kw):
            pass

    fake_w32util.ServiceFramework = _SvcBase

    fake_svc.SERVICE_RUNNING = 4
    fake_svc.SERVICE_STOPPED = 1
    fake_svc.SERVICE_STOP_PENDING = 3

    fake_event.CreateEvent = lambda *a, **kw: object()
    fake_event.SetEvent = lambda *a, **kw: None

    fake_smgr.calls = []
    fake_smgr.PrepareToHostSingle = lambda cls: fake_smgr.calls.append(("prepare", cls))
    fake_smgr.Initialize = lambda *a: fake_smgr.calls.append(("initialize", a))
    fake_smgr.StartServiceCtrlDispatcher = lambda: fake_smgr.calls.append(("dispatch",))
    fake_smgr.LogInfoMsg = lambda *_a, **_kw: None
    fake_smgr.LogErrorMsg = lambda *_a, **_kw: None

    monkeypatch.setitem(sys.modules, "win32serviceutil", fake_w32util)
    monkeypatch.setitem(sys.modules, "win32service", fake_svc)
    monkeypatch.setitem(sys.modules, "win32event", fake_event)
    monkeypatch.setitem(sys.modules, "servicemanager", fake_smgr)

    if "server_monitor_agent.service_windows" in sys.modules:
        service_windows = importlib.reload(sys.modules["server_monitor_agent.service_windows"])
    else:
        from server_monitor_agent import service_windows  # type: ignore[no-redef]

    fakes = {"smgr": fake_smgr, "svc": fake_svc, "event": fake_event, "util": fake_w32util}
    return service_windows, fakes


def test_module_exposes_service_class(monkeypatch: pytest.MonkeyPatch) -> None:
    service_windows, _ = _install_pywin32_stubs(monkeypatch)

    assert hasattr(service_windows, "AgentService")
    assert service_windows.AgentService._svc_name_ == "ServerMonitorAgent"  # noqa: SLF001
    assert service_windows.AgentService._svc_display_name_  # noqa: SLF001


def test_enter_service_dispatcher_calls_pywin32_dispatcher(monkeypatch: pytest.MonkeyPatch) -> None:
    service_windows, fakes = _install_pywin32_stubs(monkeypatch)

    rc = service_windows.enter_service_dispatcher()
    assert rc == 0

    call_names = [c[0] for c in fakes["smgr"].calls]
    assert call_names == ["prepare", "initialize", "dispatch"]


def test_enter_service_dispatcher_returns_2_when_pywin32_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    service_windows, _ = _install_pywin32_stubs(monkeypatch)
    monkeypatch.setattr(service_windows, "_PYWIN32_AVAILABLE", False)
    assert service_windows.enter_service_dispatcher() == 2
