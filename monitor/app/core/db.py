"""SQLite connection helpers and schema bootstrap."""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS servers (
    id                 INTEGER PRIMARY KEY,
    hostname           TEXT NOT NULL UNIQUE,
    os                 TEXT NOT NULL CHECK (os IN ('windows','linux')),
    enrollment_token   TEXT,
    enrollment_expires_at TEXT,
    agent_token_hash   TEXT,
    first_seen_at      TEXT NOT NULL,
    last_seen_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id                 INTEGER PRIMARY KEY,
    server_id          INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    device_name        TEXT NOT NULL,
    username           TEXT,
    protocol           TEXT NOT NULL CHECK (protocol IN ('rdp','ssh','console')),
    state              TEXT NOT NULL CHECK (state IN ('active','disconnected')),
    logon_at           TEXT NOT NULL,
    last_seen_at       TEXT NOT NULL,
    ended_at           TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_server_active ON sessions(server_id, ended_at);

CREATE TABLE IF NOT EXISTS aliases (
    device_name        TEXT PRIMARY KEY,
    alias              TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookings (
    id                 INTEGER PRIMARY KEY,
    server_id          INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    start_at           TEXT NOT NULL,
    end_at             TEXT NOT NULL,
    member_name        TEXT NOT NULL,
    note               TEXT,
    created_at         TEXT NOT NULL,
    UNIQUE(server_id, start_at)
);
CREATE INDEX IF NOT EXISTS idx_bookings_lookup ON bookings(server_id, start_at);

CREATE TABLE IF NOT EXISTS reports (
    id                 INTEGER PRIMARY KEY,
    server_id          INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    received_at        TEXT NOT NULL,
    payload_json       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_received_at ON reports(received_at);
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with sane PRAGMAs."""
    c = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript(SCHEMA)
