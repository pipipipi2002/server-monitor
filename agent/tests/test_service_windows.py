"""Smoke test that the Windows service module imports and exposes the required symbols."""

from __future__ import annotations

import sys
import types


def test_module_exposes_service_class(monkeypatch) -> None:
    """We don't run the service here; we only check the API surface.

    pywin32 isn't typically installed on the dev box, so we install fake stubs first.
    """
    fake_w32 = types.ModuleType("win32serviceutil")
    fake_svc = types.ModuleType("win32service")

    class _SvcBase:
        def __init__(self, *_a, **_kw): pass
        @classmethod
        def Install(cls, *a, **kw): pass

    fake_w32.ServiceFramework = _SvcBase
    fake_svc.SERVICE_RUNNING = 0
    fake_svc.SERVICE_STOPPED = 1
    monkeypatch.setitem(sys.modules, "win32serviceutil", fake_w32)
    monkeypatch.setitem(sys.modules, "win32service", fake_svc)
    monkeypatch.setitem(sys.modules, "win32event", types.ModuleType("win32event"))
    monkeypatch.setitem(sys.modules, "servicemanager", types.ModuleType("servicemanager"))

    from server_monitor_agent import service_windows

    assert hasattr(service_windows, "AgentService")
    assert service_windows.AgentService._svc_name_  # noqa: SLF001
