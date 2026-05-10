"""Reconcile a snapshot from the agent into the sessions table."""

from __future__ import annotations

import sqlite3


def _seed_server(conn: sqlite3.Connection) -> int:
    conn.execute(
        "INSERT INTO servers (hostname, os, first_seen_at, last_seen_at) "
        "VALUES ('h1', 'linux', '2026-05-10T00:00:00+00:00', '2026-05-10T00:00:00+00:00')"
    )
    return conn.execute("SELECT id FROM servers").fetchone()[0]


def _make_snap(device: str, state: str = "active", proto: str = "rdp", logon: str = "2026-05-10T13:00:00+00:00"):
    return {
        "device_name": device,
        "username": "shared",
        "protocol": proto,
        "state": state,
        "logon_at": logon,
    }


def test_apply_inserts_new_sessions(conn: sqlite3.Connection) -> None:
    from app.core.sessions import apply_snapshot

    sid = _seed_server(conn)
    apply_snapshot(conn, server_id=sid, sessions=[_make_snap("LAPTOP-A")], received_at="2026-05-10T13:01:00+00:00")
    rows = conn.execute(
        "SELECT device_name, state, ended_at FROM sessions WHERE server_id=?", (sid,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["device_name"] == "LAPTOP-A"
    assert rows[0]["ended_at"] is None


def test_apply_keeps_existing_session_open_and_updates_last_seen(conn: sqlite3.Connection) -> None:
    from app.core.sessions import apply_snapshot

    sid = _seed_server(conn)
    apply_snapshot(conn, server_id=sid, sessions=[_make_snap("LAPTOP-A")], received_at="2026-05-10T13:01:00+00:00")
    apply_snapshot(conn, server_id=sid, sessions=[_make_snap("LAPTOP-A")], received_at="2026-05-10T13:05:00+00:00")
    rows = conn.execute(
        "SELECT id, last_seen_at, ended_at FROM sessions WHERE server_id=?", (sid,)
    ).fetchall()
    assert len(rows) == 1, "should not insert a duplicate row for the same logical session"
    assert rows[0]["ended_at"] is None
    assert rows[0]["last_seen_at"] == "2026-05-10T13:05:00+00:00"


def test_apply_marks_disappeared_session_as_ended(conn: sqlite3.Connection) -> None:
    from app.core.sessions import apply_snapshot

    sid = _seed_server(conn)
    apply_snapshot(conn, server_id=sid, sessions=[_make_snap("LAPTOP-A")], received_at="2026-05-10T13:01:00+00:00")
    apply_snapshot(conn, server_id=sid, sessions=[], received_at="2026-05-10T13:10:00+00:00")
    row = conn.execute("SELECT ended_at FROM sessions WHERE server_id=?", (sid,)).fetchone()
    assert row["ended_at"] == "2026-05-10T13:10:00+00:00"


def test_apply_state_change_updates_in_place(conn: sqlite3.Connection) -> None:
    from app.core.sessions import apply_snapshot

    sid = _seed_server(conn)
    apply_snapshot(
        conn, server_id=sid,
        sessions=[_make_snap("LAPTOP-A", state="active")],
        received_at="2026-05-10T13:01:00+00:00",
    )
    apply_snapshot(
        conn, server_id=sid,
        sessions=[_make_snap("LAPTOP-A", state="disconnected")],
        received_at="2026-05-10T13:05:00+00:00",
    )
    rows = conn.execute(
        "SELECT state FROM sessions WHERE server_id=? AND ended_at IS NULL", (sid,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["state"] == "disconnected"


def test_apply_returns_diff_summary(conn: sqlite3.Connection) -> None:
    from app.core.sessions import apply_snapshot

    sid = _seed_server(conn)
    diff = apply_snapshot(
        conn, server_id=sid,
        sessions=[_make_snap("A"), _make_snap("B")],
        received_at="2026-05-10T13:01:00+00:00",
    )
    assert sorted(d["device_name"] for d in diff.added) == ["A", "B"]
    assert diff.ended == [] and diff.changed == []

    diff = apply_snapshot(
        conn, server_id=sid,
        sessions=[_make_snap("A", state="disconnected")],
        received_at="2026-05-10T13:05:00+00:00",
    )
    assert [d["device_name"] for d in diff.changed] == ["A"]
    assert [d["device_name"] for d in diff.ended] == ["B"]
    assert diff.added == []


def test_list_active_sessions_returns_only_open(conn: sqlite3.Connection) -> None:
    from app.core.sessions import apply_snapshot, list_active_sessions

    sid = _seed_server(conn)
    apply_snapshot(conn, server_id=sid, sessions=[_make_snap("A"), _make_snap("B")], received_at="2026-05-10T13:01:00+00:00")
    apply_snapshot(conn, server_id=sid, sessions=[_make_snap("A")], received_at="2026-05-10T13:05:00+00:00")
    active = list_active_sessions(conn, server_id=sid)
    assert [a["device_name"] for a in active] == ["A"]
