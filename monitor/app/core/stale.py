"""Detect servers whose agents haven't reported recently."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from app.core.clock import now, parse_iso


def check_stale(conn: sqlite3.Connection, *, threshold_seconds: int) -> list[dict]:
    cutoff = now() - timedelta(seconds=threshold_seconds)
    rows = conn.execute("SELECT id, hostname, last_seen_at FROM servers").fetchall()
    return [
        {"server_id": r["id"], "hostname": r["hostname"], "last_seen_at": r["last_seen_at"]}
        for r in rows
        if parse_iso(r["last_seen_at"]) < cutoff
    ]
