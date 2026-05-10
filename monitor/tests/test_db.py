"""Schema bootstrapping and PRAGMA defaults."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def test_connect_enables_foreign_keys(db_path: Path) -> None:
    from app.core.db import connect

    c = connect(db_path)
    (fk,) = c.execute("PRAGMA foreign_keys").fetchone()
    assert fk == 1


def test_connect_uses_wal_mode(db_path: Path) -> None:
    from app.core.db import connect

    c = connect(db_path)
    (mode,) = c.execute("PRAGMA journal_mode").fetchone()
    assert mode == "wal"


def test_init_schema_creates_all_tables(db_path: Path) -> None:
    from app.core.db import connect, init_schema

    c = connect(db_path)
    init_schema(c)
    rows = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"servers", "sessions", "aliases", "bookings", "reports"}.issubset(names)


def test_init_schema_is_idempotent(db_path: Path) -> None:
    from app.core.db import connect, init_schema

    c = connect(db_path)
    init_schema(c)
    init_schema(c)  # must not raise


def test_servers_hostname_is_unique(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO servers (hostname, os, first_seen_at, last_seen_at) "
        "VALUES ('h1', 'linux', '2026-05-10T00:00:00+00:00', '2026-05-10T00:00:00+00:00')"
    )
    try:
        conn.execute(
            "INSERT INTO servers (hostname, os, first_seen_at, last_seen_at) "
            "VALUES ('h1', 'linux', '2026-05-10T00:00:00+00:00', '2026-05-10T00:00:00+00:00')"
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised


def test_bookings_unique_per_slot(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO servers (hostname, os, first_seen_at, last_seen_at) "
        "VALUES ('h1', 'linux', '2026-05-10T00:00:00+00:00', '2026-05-10T00:00:00+00:00')"
    )
    sid = conn.execute("SELECT id FROM servers").fetchone()[0]
    conn.execute(
        "INSERT INTO bookings (server_id, start_at, end_at, member_name, created_at) "
        "VALUES (?, '2026-05-10T14:00:00+00:00', '2026-05-10T14:30:00+00:00', 'alice', '2026-05-10T13:00:00+00:00')",
        (sid,),
    )
    try:
        conn.execute(
            "INSERT INTO bookings (server_id, start_at, end_at, member_name, created_at) "
            "VALUES (?, '2026-05-10T14:00:00+00:00', '2026-05-10T14:30:00+00:00', 'bob', '2026-05-10T13:00:00+00:00')",
            (sid,),
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised
