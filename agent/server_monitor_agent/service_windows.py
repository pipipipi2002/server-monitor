"""Windows Service wrapper. Imported only on Windows; pywin32 must be installed.

Install / start (run from an admin shell):
    server-monitor-agent-service.exe install
    sc start ServerMonitorAgent
"""

from __future__ import annotations

import asyncio
import sys

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
except ImportError:  # type: ignore[unreachable]
    # Module imported on a non-Windows host (tests, or accidental import).
    win32serviceutil = None  # type: ignore[assignment]


from server_monitor_agent.__main__ import main as cli_main


class AgentService(win32serviceutil.ServiceFramework if win32serviceutil else object):  # type: ignore[misc]
    _svc_name_ = "ServerMonitorAgent"
    _svc_display_name_ = "Server Monitor Agent"
    _svc_description_ = "Reports RDP session activity to the server-monitor service."

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self._stop = win32event.CreateEvent(None, 0, 0, None)
        self._task: asyncio.Task | None = None

    def SvcStop(self):  # noqa: N802
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self._stop)

    def SvcDoRun(self):  # noqa: N802
        servicemanager.LogInfoMsg(f"{self._svc_name_} starting")
        # Delegate to the CLI so settings flow the same way as a manual run.
        sys.argv = ["server-monitor-agent", "run"]
        try:
            cli_main(sys.argv[1:])
        except SystemExit:
            pass


if __name__ == "__main__":
    if win32serviceutil:
        win32serviceutil.HandleCommandLine(AgentService)
    else:
        print("pywin32 is required on Windows", file=sys.stderr)
        raise SystemExit(2)
