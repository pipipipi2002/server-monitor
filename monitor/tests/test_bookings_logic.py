"""Pure booking logic — slot validation + conflict detection against DB."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest


def _seed_server(conn: sqlite3.Connection) -> int:
    conn.execute(
        "INSERT INTO servers (hostname, os, first_seen_at, last_seen_at) "
        "VALUES ('h1', 'linux', '2026-05-10T00:00:00+00:00', '2026-05-10T00:00:00+00:00')"
    )
    return conn.execute("SELECT id FROM servers").fetchone()[0]


def test_create_booking_round_trips(conn: sqlite3.Connection) -> None:
    from app.core.bookings import create_booking

    sid = _seed_server(conn)
    start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
    bid = create_booking(conn, server_id=sid, start_at=start, member_name="alice", note=None)
    row = conn.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
    assert row["member_name"] == "alice"
    assert row["start_at"] == "2030-01-01T14:00:00+00:00"
    assert row["end_at"] == "2030-01-01T14:30:00+00:00"


def test_create_booking_rejects_unaligned_slot(conn: sqlite3.Connection) -> None:
    from app.core.bookings import BookingError, create_booking

    sid = _seed_server(conn)
    start = datetime(2030, 1, 1, 14, 15, tzinfo=UTC)
    with pytest.raises(BookingError, match="slot"):
        create_booking(conn, server_id=sid, start_at=start, member_name="alice", note=None)


def test_create_booking_rejects_past_slot(conn: sqlite3.Connection) -> None:
    from app.core.bookings import BookingError, create_booking

    sid = _seed_server(conn)
    start = datetime(2000, 1, 1, 14, 0, tzinfo=UTC)
    with pytest.raises(BookingError, match="past"):
        create_booking(conn, server_id=sid, start_at=start, member_name="alice", note=None)


def test_create_booking_rejects_beyond_horizon(conn: sqlite3.Connection) -> None:
    from app.core.bookings import BookingError, create_booking

    sid = _seed_server(conn)
    # Eight days past FIXED_NOW (2029-12-31 12:00 UTC) → 2030-01-08 14:00, beyond the 7-day horizon.
    start = datetime(2030, 1, 8, 14, 0, tzinfo=UTC)
    with pytest.raises(BookingError, match="horizon"):
        create_booking(conn, server_id=sid, start_at=start, member_name="alice", note=None)


def test_create_booking_rejects_empty_member_name(conn: sqlite3.Connection) -> None:
    from app.core.bookings import BookingError, create_booking

    sid = _seed_server(conn)
    start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
    with pytest.raises(BookingError, match="member"):
        create_booking(conn, server_id=sid, start_at=start, member_name="   ", note=None)


def test_create_booking_conflict_returns_specific_error(conn: sqlite3.Connection) -> None:
    from app.core.bookings import BookingConflict, create_booking

    sid = _seed_server(conn)
    start = datetime(2030, 1, 1, 14, 0, tzinfo=UTC)
    create_booking(conn, server_id=sid, start_at=start, member_name="alice", note=None)
    with pytest.raises(BookingConflict):
        create_booking(conn, server_id=sid, start_at=start, member_name="bob", note=None)


def test_delete_booking_returns_true_on_hit_false_on_miss(conn: sqlite3.Connection) -> None:
    from app.core.bookings import create_booking, delete_booking

    sid = _seed_server(conn)
    bid = create_booking(
        conn,
        server_id=sid,
        start_at=datetime(2030, 1, 1, 14, 0, tzinfo=UTC),
        member_name="alice",
        note=None,
    )
    assert delete_booking(conn, bid) is True
    assert delete_booking(conn, bid) is False


def test_list_bookings_for_day_returns_only_that_day(conn: sqlite3.Connection) -> None:
    from app.core.bookings import create_booking, list_bookings_for_day

    sid = _seed_server(conn)
    create_booking(
        conn, server_id=sid,
        start_at=datetime(2030, 1, 1, 14, 0, tzinfo=UTC),
        member_name="alice", note=None,
    )
    create_booking(
        conn, server_id=sid,
        start_at=datetime(2030, 1, 2, 14, 0, tzinfo=UTC),
        member_name="bob", note=None,
    )
    rows = list_bookings_for_day(conn, server_id=sid, day=datetime(2030, 1, 1, tzinfo=UTC).date())
    assert len(rows) == 1
    assert rows[0]["member_name"] == "alice"
