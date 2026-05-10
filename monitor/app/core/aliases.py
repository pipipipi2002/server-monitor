"""Device-name → human-alias map. Public, no auth."""

from __future__ import annotations

import sqlite3

from app.core.clock import now_iso


def upsert_alias(conn: sqlite3.Connection, *, device_name: str, alias: str) -> None:
    cleaned = alias.strip()
    if not cleaned:
        raise ValueError("alias must not be empty")
    conn.execute(
        """
        INSERT INTO aliases (device_name, alias, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(device_name) DO UPDATE SET alias=excluded.alias, updated_at=excluded.updated_at
        """,
        (device_name, cleaned, now_iso()),
    )


def get_alias(conn: sqlite3.Connection, device_name: str) -> str | None:
    row = conn.execute("SELECT alias FROM aliases WHERE device_name=?", (device_name,)).fetchone()
    return row["alias"] if row else None


def list_aliases(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT device_name, alias, updated_at FROM aliases ORDER BY alias COLLATE NOCASE"
    ).fetchall()


def known_members(conn: sqlite3.Connection) -> list[str]:
    """Distinct alias values, sorted, for booking autocomplete."""
    rows = conn.execute(
        "SELECT DISTINCT alias FROM aliases ORDER BY alias COLLATE NOCASE"
    ).fetchall()
    return [r["alias"] for r in rows]
