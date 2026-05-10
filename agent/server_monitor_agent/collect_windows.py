"""Read RDP sessions on Windows via the WTS API.

We use pywin32's win32ts module. Logon time isn't directly returned by
WTSQuerySessionInformation in older Windows builds, so we approximate by
remembering the first time we saw a (session_id, client_name) tuple.
This is good enough for "minutes ago" display.

Verified manually on Windows Server 2019/2022 — see Phase 8.
"""

from __future__ import annotations

from datetime import UTC, datetime

from server_monitor_agent.snapshot import Session


_FIRST_SEEN: dict[tuple[int, str], str] = {}


def _state_to_string(value: int) -> str:
    # WTSActive == 0; WTSDisconnected == 4; everything else is treated as active for our purposes.
    return "disconnected" if value == 4 else "active"


def collect() -> list[Session]:
    try:
        import win32ts  # type: ignore[import-not-found]
    except ImportError:
        return []

    out: list[Session] = []
    handle = win32ts.WTS_CURRENT_SERVER_HANDLE
    for session_id, _name, state in win32ts.WTSEnumerateSessions(handle):
        try:
            client_name = win32ts.WTSQuerySessionInformation(handle, session_id, win32ts.WTSClientName) or ""
            user = win32ts.WTSQuerySessionInformation(handle, session_id, win32ts.WTSUserName) or ""
        except Exception:  # noqa: BLE001
            continue
        if not client_name:
            continue  # services session, console without remote client, etc.
        key = (session_id, client_name)
        first = _FIRST_SEEN.setdefault(key, datetime.now(UTC).isoformat())
        out.append({
            "device_name": client_name,
            "username": user or None,
            "protocol": "rdp",
            "state": _state_to_string(state),
            "logon_at": first,
        })
    # Drop stale first-seen entries no longer present in this enumeration.
    seen = {(sess["device_name"],) for sess in out}  # noqa: F841
    for key in list(_FIRST_SEEN.keys()):
        if key[1] not in {s["device_name"] for s in out}:
            _FIRST_SEEN.pop(key, None)
    return out
