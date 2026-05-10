"""Server enrollment and token verification helpers."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest


def test_create_pending_server_stores_enrollment_token(conn: sqlite3.Connection) -> None:
    from app.core.servers import create_pending_server

    sid, token = create_pending_server(conn, hostname="srv-a", os="linux", ttl_seconds=3600)
    row = conn.execute("SELECT * FROM servers WHERE id=?", (sid,)).fetchone()
    assert row["hostname"] == "srv-a"
    assert row["enrollment_token"] is not None
    assert row["enrollment_token"] != token, "stored value must be a hash, not plaintext"


def test_complete_enrollment_consumes_token_and_returns_agent_token(conn: sqlite3.Connection) -> None:
    from app.core.servers import complete_enrollment, create_pending_server

    sid, enroll_token = create_pending_server(conn, hostname="srv-a", os="linux", ttl_seconds=3600)
    agent_token = complete_enrollment(conn, hostname="srv-a", enrollment_token=enroll_token)

    row = conn.execute("SELECT * FROM servers WHERE id=?", (sid,)).fetchone()
    assert row["enrollment_token"] is None
    assert row["agent_token_hash"] is not None
    assert agent_token  # plaintext returned to caller; never stored as plaintext


def test_complete_enrollment_rejects_wrong_token(conn: sqlite3.Connection) -> None:
    from app.core.servers import EnrollmentError, complete_enrollment, create_pending_server

    create_pending_server(conn, hostname="srv-a", os="linux", ttl_seconds=3600)
    with pytest.raises(EnrollmentError):
        complete_enrollment(conn, hostname="srv-a", enrollment_token="not-the-token")


def test_complete_enrollment_rejects_expired_token(conn: sqlite3.Connection) -> None:
    from app.core.servers import EnrollmentError, complete_enrollment, create_pending_server

    sid, token = create_pending_server(conn, hostname="srv-a", os="linux", ttl_seconds=3600)
    past = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    conn.execute("UPDATE servers SET enrollment_expires_at=? WHERE id=?", (past, sid))
    with pytest.raises(EnrollmentError, match="expired"):
        complete_enrollment(conn, hostname="srv-a", enrollment_token=token)


def test_authenticate_agent_returns_server_id_on_match(conn: sqlite3.Connection) -> None:
    from app.core.servers import authenticate_agent, complete_enrollment, create_pending_server

    sid, enroll = create_pending_server(conn, hostname="srv-a", os="linux", ttl_seconds=3600)
    agent_token = complete_enrollment(conn, hostname="srv-a", enrollment_token=enroll)
    assert authenticate_agent(conn, hostname="srv-a", token=agent_token) == sid


def test_authenticate_agent_returns_none_for_wrong_token(conn: sqlite3.Connection) -> None:
    from app.core.servers import authenticate_agent, create_pending_server

    create_pending_server(conn, hostname="srv-a", os="linux", ttl_seconds=3600)
    # We can't reuse plaintext easily; simulate by re-enrolling with a known token via direct call
    from app.core.tokens import generate_token, hash_token
    new_plain = generate_token()
    conn.execute(
        "UPDATE servers SET agent_token_hash=?, enrollment_token=NULL WHERE hostname='srv-a'",
        (hash_token(new_plain),),
    )
    assert authenticate_agent(conn, hostname="srv-a", token="wrong") is None
    assert authenticate_agent(conn, hostname="srv-a", token=new_plain) is not None


def test_reset_server_regenerates_enrollment_token(conn: sqlite3.Connection) -> None:
    from app.core.servers import complete_enrollment, create_pending_server, reset_server

    sid, enroll = create_pending_server(conn, hostname="srv-a", os="linux", ttl_seconds=3600)
    complete_enrollment(conn, hostname="srv-a", enrollment_token=enroll)
    new_token = reset_server(conn, server_id=sid, ttl_seconds=3600)
    row = conn.execute("SELECT * FROM servers WHERE id=?", (sid,)).fetchone()
    assert row["enrollment_token"] is not None
    assert row["agent_token_hash"] is None
    assert new_token  # plaintext returned

def test_list_servers_orders_by_hostname(conn: sqlite3.Connection) -> None:
    from app.core.servers import create_pending_server, list_servers

    create_pending_server(conn, hostname="b", os="linux", ttl_seconds=3600)
    create_pending_server(conn, hostname="a", os="linux", ttl_seconds=3600)
    rows = list_servers(conn)
    assert [r["hostname"] for r in rows] == ["a", "b"]
