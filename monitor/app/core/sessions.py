"""Reconcile reported sessions against the live `sessions` table.

A "logical session" is identified by (server_id, device_name) while ended_at IS NULL.
- New device in snapshot → INSERT row.
- Existing device → UPDATE last_seen_at (and state if changed).
- Existing device missing from snapshot → mark ended_at = received_at.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import TypedDict


class SessionInput(TypedDict):
    device_name: str
    username: str | None
    protocol: str  # 'rdp' | 'ssh' | 'console'
    state: str     # 'active' | 'disconnected'
    logon_at: str  # ISO-8601 UTC


@dataclass
class Diff:
    added: list[dict] = field(default_factory=list)
    changed: list[dict] = field(default_factory=list)
    ended: list[dict] = field(default_factory=list)


def apply_snapshot(
    conn: sqlite3.Connection,
    *,
    server_id: int,
    sessions: list[SessionInput],
    received_at: str,
) -> Diff:
    diff = Diff()
    open_rows = conn.execute(
        "SELECT id, device_name, state FROM sessions WHERE server_id=? AND ended_at IS NULL",
        (server_id,),
    ).fetchall()
    open_by_device = {r["device_name"]: r for r in open_rows}
    seen: set[str] = set()

    for s in sessions:
        device = s["device_name"]
        seen.add(device)
        existing = open_by_device.get(device)
        if existing is None:
            conn.execute(
                """
                INSERT INTO sessions
                  (server_id, device_name, username, protocol, state, logon_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    server_id, device, s.get("username"), s["protocol"], s["state"],
                    s["logon_at"], received_at,
                ),
            )
            diff.added.append({"device_name": device, "state": s["state"], "logon_at": s["logon_at"]})
        else:
            if existing["state"] != s["state"]:
                conn.execute(
                    "UPDATE sessions SET state=?, last_seen_at=? WHERE id=?",
                    (s["state"], received_at, existing["id"]),
                )
                diff.changed.append({"device_name": device, "state": s["state"]})
            else:
                conn.execute(
                    "UPDATE sessions SET last_seen_at=? WHERE id=?",
                    (received_at, existing["id"]),
                )

    for device, row in open_by_device.items():
        if device not in seen:
            conn.execute(
                "UPDATE sessions SET ended_at=?, last_seen_at=? WHERE id=?",
                (received_at, received_at, row["id"]),
            )
            diff.ended.append({"device_name": device})

    conn.execute("UPDATE servers SET last_seen_at=? WHERE id=?", (received_at, server_id))
    return diff


def list_active_sessions(conn: sqlite3.Connection, *, server_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM sessions
        WHERE server_id=? AND ended_at IS NULL
        ORDER BY CASE state WHEN 'active' THEN 0 ELSE 1 END, logon_at
        """,
        (server_id,),
    ).fetchall()
