"""Pure booking logic — validation, conflict detection, queries.

Bookings are 30-minute slots aligned to :00 or :30, must be in the future,
no further than 7 days ahead, and unique per (server_id, start_at).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta

from app.core.clock import SLOT, is_slot_aligned, now, now_iso

HORIZON = timedelta(days=7)


class BookingError(ValueError):
    """Validation problem with a booking request."""


class BookingConflict(BookingError):
    """The slot is already taken."""


def _validate(start_at: datetime, member_name: str) -> None:
    if not is_slot_aligned(start_at):
        raise BookingError("slot must be aligned to :00 or :30, no seconds")
    if start_at < now():
        raise BookingError("cannot book a slot in the past")
    if start_at > now() + HORIZON:
        raise BookingError("cannot book beyond the 7-day horizon")
    if not member_name or not member_name.strip():
        raise BookingError("member name is required")


def create_booking(
    conn: sqlite3.Connection,
    *,
    server_id: int,
    start_at: datetime,
    member_name: str,
    note: str | None,
) -> int:
    """Insert a booking and return its id. Raises BookingError on validation."""
    _validate(start_at, member_name)
    end_at = start_at + SLOT
    try:
        cur = conn.execute(
            """
            INSERT INTO bookings (server_id, start_at, end_at, member_name, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                server_id,
                start_at.astimezone(UTC).isoformat(),
                end_at.astimezone(UTC).isoformat(),
                member_name.strip(),
                (note or "").strip() or None,
                now_iso(),
            ),
        )
    except sqlite3.IntegrityError as e:
        if "bookings" in str(e):
            raise BookingConflict("slot already booked") from e
        raise
    return int(cur.lastrowid)


def delete_booking(conn: sqlite3.Connection, booking_id: int) -> bool:
    cur = conn.execute("DELETE FROM bookings WHERE id=?", (booking_id,))
    return cur.rowcount > 0


def list_bookings_for_day(
    conn: sqlite3.Connection, *, server_id: int, day: date
) -> list[sqlite3.Row]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC).isoformat()
    end = (datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1)).isoformat()
    return conn.execute(
        """
        SELECT * FROM bookings
        WHERE server_id=? AND start_at >= ? AND start_at < ?
        ORDER BY start_at
        """,
        (server_id, start, end),
    ).fetchall()
