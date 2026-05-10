"""Server enrollment lifecycle + agent authentication."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from app.core.clock import now, now_iso, parse_iso
from app.core.tokens import generate_token, hash_token, verify_token


class EnrollmentError(ValueError):
    """Enrollment token mismatch or expiry."""


def create_pending_server(
    conn: sqlite3.Connection, *, hostname: str, os: str, ttl_seconds: int
) -> tuple[int, str]:
    """Insert (or reuse) a pending server, return (id, plaintext enrollment token)."""
    plain = generate_token()
    expires = (now() + timedelta(seconds=ttl_seconds)).isoformat()
    cur = conn.execute(
        """
        INSERT INTO servers (hostname, os, enrollment_token, enrollment_expires_at,
                             first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(hostname) DO UPDATE SET
            os=excluded.os,
            enrollment_token=excluded.enrollment_token,
            enrollment_expires_at=excluded.enrollment_expires_at,
            agent_token_hash=NULL
        """,
        (hostname, os, hash_token(plain), expires, now_iso(), now_iso()),
    )
    sid = cur.lastrowid or conn.execute(
        "SELECT id FROM servers WHERE hostname=?", (hostname,)
    ).fetchone()[0]
    return int(sid), plain


def complete_enrollment(
    conn: sqlite3.Connection, *, hostname: str, enrollment_token: str
) -> str:
    """Validate enrollment token and return a fresh agent token (plaintext)."""
    row = conn.execute(
        "SELECT id, enrollment_token, enrollment_expires_at FROM servers WHERE hostname=?",
        (hostname,),
    ).fetchone()
    if row is None or row["enrollment_token"] is None:
        raise EnrollmentError("no pending enrollment for this hostname")
    if parse_iso(row["enrollment_expires_at"]) < now():
        raise EnrollmentError("enrollment token expired")
    if not verify_token(enrollment_token, row["enrollment_token"]):
        raise EnrollmentError("enrollment token mismatch")

    agent_plain = generate_token()
    conn.execute(
        """
        UPDATE servers
        SET agent_token_hash=?, enrollment_token=NULL, enrollment_expires_at=NULL,
            last_seen_at=?
        WHERE id=?
        """,
        (hash_token(agent_plain), now_iso(), row["id"]),
    )
    return agent_plain


def authenticate_agent(
    conn: sqlite3.Connection, *, hostname: str, token: str
) -> int | None:
    row = conn.execute(
        "SELECT id, agent_token_hash FROM servers WHERE hostname=?", (hostname,)
    ).fetchone()
    if row is None or not row["agent_token_hash"]:
        return None
    return int(row["id"]) if verify_token(token, row["agent_token_hash"]) else None


def reset_server(conn: sqlite3.Connection, *, server_id: int, ttl_seconds: int) -> str:
    plain = generate_token()
    expires = (now() + timedelta(seconds=ttl_seconds)).isoformat()
    conn.execute(
        """
        UPDATE servers
        SET enrollment_token=?, enrollment_expires_at=?, agent_token_hash=NULL,
            last_seen_at=?
        WHERE id=?
        """,
        (hash_token(plain), expires, now_iso(), server_id),
    )
    return plain


def list_servers(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, hostname, os, agent_token_hash IS NOT NULL AS enrolled, "
        "first_seen_at, last_seen_at FROM servers ORDER BY hostname COLLATE NOCASE"
    ).fetchall()
