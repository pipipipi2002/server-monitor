"""Windows Service wrapper.

When the bundled `agent-windows.exe` is launched by SCM with no command-line
arguments (which is how SCM starts a service), `__main__.main()` detects this
and calls `enter_service_dispatcher()` from this module. That hands off to
pywin32's `StartServiceCtrlDispatcher`, which:

  1. Connects to SCM and registers `AgentService`.
  2. Calls `AgentService.SvcDoRun()` on a worker thread.
  3. Reports `SERVICE_RUNNING` to SCM, so we don't get killed for the
     "service did not start in a timely manner" timeout.

`SvcDoRun` reads agent configuration from environment variables (the
install.ps1 script writes these into the service's registry environment block):

    MONITOR_URL         e.g. https://monitor.lan
    MONITOR_HOSTNAME    optional; defaults to socket.gethostname()
    MONITOR_TOKEN_FILE  path to the agent token file
    MONITOR_CA_BUNDLE   path to the monitor's CA cert

This module imports cleanly on non-Windows hosts because the pywin32 imports
are inside try/except (so test_service_windows.py can run on the Linux dev box
by injecting fake stub modules).
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path

try:
    import servicemanager  # type: ignore[import-not-found]
    import win32event  # type: ignore[import-not-found]
    import win32service  # type: ignore[import-not-found]
    import win32serviceutil  # type: ignore[import-not-found]
    _PYWIN32_AVAILABLE = True
except ImportError:
    _PYWIN32_AVAILABLE = False
    win32serviceutil = None  # type: ignore[assignment]


_BASE = win32serviceutil.ServiceFramework if _PYWIN32_AVAILABLE else object


class AgentService(_BASE):  # type: ignore[misc,valid-type]
    _svc_name_ = "ServerMonitorAgent"
    _svc_display_name_ = "Server Monitor Agent"
    _svc_description_ = "Reports RDP session activity to the server-monitor service."

    def __init__(self, args):
        if _PYWIN32_AVAILABLE:
            win32serviceutil.ServiceFramework.__init__(self, args)
            self._stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    def SvcStop(self):  # noqa: N802
        servicemanager.LogInfoMsg(f"{self._svc_name_}: stop requested")
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        # Cancel the report loop in a thread-safe way; SCM's stop signal arrives
        # on a different thread than the asyncio loop is running on.
        if self._loop and self._task and not self._task.done():
            self._loop.call_soon_threadsafe(self._task.cancel)
        win32event.SetEvent(self._stop_event)

    def SvcDoRun(self):  # noqa: N802
        servicemanager.LogInfoMsg(f"{self._svc_name_}: starting")
        try:
            self._run_until_stopped()
        except Exception as e:  # noqa: BLE001
            servicemanager.LogErrorMsg(f"{self._svc_name_}: fatal: {e!r}")
            raise
        servicemanager.LogInfoMsg(f"{self._svc_name_}: stopped")

    def _run_until_stopped(self) -> None:
        # Lazy imports keep the module importable on non-Windows hosts (the
        # PyInstaller Linux spec excludes service_windows entirely).
        from server_monitor_agent.client import Client
        from server_monitor_agent.run import run_loop
        from server_monitor_agent.token_store import default_token_path, load_token

        monitor_url = os.environ.get("MONITOR_URL")
        if not monitor_url:
            raise RuntimeError(
                "MONITOR_URL not set; install.ps1 should write it into the "
                "service's registry environment block under "
                r"HKLM\SYSTEM\CurrentControlSet\Services\ServerMonitorAgent\Environment"
            )
        hostname = os.environ.get("MONITOR_HOSTNAME") or socket.gethostname()
        token_file_str = os.environ.get("MONITOR_TOKEN_FILE")
        token_file = Path(token_file_str) if token_file_str else default_token_path()
        ca_bundle = os.environ.get("MONITOR_CA_BUNDLE") or None

        token = load_token(token_file)
        if not token:
            raise RuntimeError(f"no token at {token_file}; re-enroll required")

        verify: bool | str = ca_bundle if ca_bundle else True
        client = Client(base_url=monitor_url, verify=verify)

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._task = self._loop.create_task(
                run_loop(client=client, hostname=hostname, token=token)
            )
            try:
                self._loop.run_until_complete(self._task)
            except asyncio.CancelledError:
                # SvcStop was called; clean shutdown.
                pass
            self._loop.run_until_complete(client.aclose())
        finally:
            self._loop.close()
            self._loop = None


def enter_service_dispatcher() -> int:
    """Connect to SCM and dispatch to AgentService.

    Called by `__main__.main()` when the binary is invoked with no arguments —
    which is how SCM launches a service. Returns an integer exit code (only
    reached if the dispatcher fails to connect; on a successful service run
    StartServiceCtrlDispatcher blocks until SCM signals stop).
    """
    if not _PYWIN32_AVAILABLE:
        print("error: pywin32 is required for Windows service mode", file=sys.stderr)
        return 2

    servicemanager.PrepareToHostSingle(AgentService)
    servicemanager.Initialize(AgentService._svc_name_, None)
    try:
        servicemanager.StartServiceCtrlDispatcher()
    except Exception as e:  # noqa: BLE001
        # ERROR_FAILED_SERVICE_CONTROLLER_CONNECT (1063): not running under SCM.
        # Surface a useful diagnostic instead of a raw COM error.
        print(
            f"failed to connect to Service Control Manager: {e!r}\n"
            "this binary should only be invoked by SCM (no args). For interactive "
            "runs use the 'run' subcommand.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    # Pywin32-style command-line entry retained for completeness; lets you do
    # `agent-windows.exe install` / `start` / `stop` / `remove` interactively.
    # Not used by install.ps1 (it goes through New-Service + sc.exe instead).
    if win32serviceutil:
        win32serviceutil.HandleCommandLine(AgentService)
    else:
        print("pywin32 is required on Windows", file=sys.stderr)
        raise SystemExit(2)
