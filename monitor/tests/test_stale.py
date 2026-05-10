"""Stale-server detection: any server unseen for > threshold goes 'offline'."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta


def test_check_stale_emits_offline_for_old_servers(conn: sqlite3.Connection, fixed_clock: datetime) -> None:
    from app.core.stale import check_stale

    long_ago = (fixed_clock - timedelta(seconds=90)).isoformat()
    fresh = fixed_clock.isoformat()
    conn.execute(
        "INSERT INTO servers (hostname, os, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)",
        ("old", "linux", long_ago, long_ago),
    )
    conn.execute(
        "INSERT INTO servers (hostname, os, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)",
        ("new", "linux", fresh, fresh),
    )
    events = check_stale(conn, threshold_seconds=60)
    hosts = {e["hostname"] for e in events}
    assert "old" in hosts
    assert "new" not in hosts
