"""Read RDP sessions on Windows via the WTS API.

We use pywin32's win32ts module. Logon time isn't directly returned by
WTSQuerySessionInformation in older Windows builds, so we approximate by
remembering the first time we saw a (session_id, device_name) tuple.

A session is recognised as RDP if its WinStation name starts with "rdp-"
(e.g., "rdp-tcp#0"), which lets us correctly skip the Services and console
sessions even when their state happens to be Active.

For the device name we prefer WTSClientName (the connecting host's NetBIOS
name, what mstsc sends). Non-Windows RDP clients — Microsoft Remote Desktop
for Mac/iOS, FreeRDP, etc. — sometimes send an empty WTSClientName, so we
fall back to:
    1. WTSClientAddress  (the connecting client's IP)
    2. WinStationName    (e.g., "rdp-tcp#111")

Verified manually on Windows Server 2019/2022 — see Phase 8.
"""

from __future__ import annotations

from datetime import UTC, datetime

from server_monitor_agent.snapshot import Session


_FIRST_SEEN: dict[tuple[int, str], str] = {}

# WTSConnectState values
_WTS_LISTEN = 6
# WTSQuerySessionInformation info classes (pywin32 doesn't expose every one
# as a named constant, so we use the documented integer codes).
_WTS_CLIENT_ADDRESS = 14


def _state_to_string(value: int) -> str:
    # WTSActive == 0; WTSDisconnected == 4. Anything else is treated as active.
    return "disconnected" if value == 4 else "active"


def _format_client_address(addr: object) -> str:
    """Format a WTSClientAddress payload as a human-readable IP, if possible.

    pywin32 returns WTSClientAddress as a tuple (AddressFamily, raw_bytes).
    AddressFamily 2 == AF_INET (IPv4); the first 4 bytes of `raw_bytes` are
    the address. Anything else, we render as best-effort.
    """
    try:
        if isinstance(addr, tuple) and len(addr) == 2:
            family, raw = addr
            if family == 2 and isinstance(raw, (bytes, bytearray)) and len(raw) >= 4:
                return ".".join(str(b) for b in raw[:4])
            if isinstance(raw, (bytes, bytearray)):
                return raw.hex()
        return str(addr) if addr else ""
    except Exception:  # noqa: BLE001
        return ""


def collect() -> list[Session]:
    try:
        import win32ts  # type: ignore[import-not-found]
    except ImportError:
        return []

    out: list[Session] = []
    handle = win32ts.WTS_CURRENT_SERVER_HANDLE

    for session_id, winstation_name, state in win32ts.WTSEnumerateSessions(handle):
        # Skip the listener pseudo-session (state == WTSListen).
        if state == _WTS_LISTEN:
            continue

        # Only RDP sessions; skip Services / console / X11.
        if not winstation_name or not winstation_name.lower().startswith("rdp"):
            continue

        try:
            client_name = (
                win32ts.WTSQuerySessionInformation(handle, session_id, win32ts.WTSClientName) or ""
            )
            user = (
                win32ts.WTSQuerySessionInformation(handle, session_id, win32ts.WTSUserName) or ""
            )
        except Exception:  # noqa: BLE001
            continue

        # No logged-in user means it's e.g. a session at the login screen — skip.
        if not user:
            continue

        # Fallback chain for device_name when the RDP client didn't send a
        # NetBIOS name (common with macOS, iOS, FreeRDP, Tailscale-routed
        # connections from non-Windows clients).
        device_name = client_name
        if not device_name:
            try:
                addr = win32ts.WTSQuerySessionInformation(
                    handle, session_id, _WTS_CLIENT_ADDRESS
                )
                device_name = _format_client_address(addr)
            except Exception:  # noqa: BLE001
                device_name = ""
        if not device_name:
            device_name = winstation_name  # e.g., "rdp-tcp#111"

        key = (session_id, device_name)
        first = _FIRST_SEEN.setdefault(key, datetime.now(UTC).isoformat())
        out.append(
            {
                "device_name": device_name,
                "username": user or None,
                "protocol": "rdp",
                "state": _state_to_string(state),
                "logon_at": first,
            }
        )

    # Drop stale first-seen entries no longer present in this enumeration.
    current_devices = {s["device_name"] for s in out}
    for key in list(_FIRST_SEEN.keys()):
        if key[1] not in current_devices:
            _FIRST_SEEN.pop(key, None)

    return out
