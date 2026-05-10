"""UTC clock helpers and 30-minute slot maths."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def now() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return now().isoformat()


def parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 string, normalised to UTC."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def floor_to_slot(dt: datetime) -> datetime:
    """Round down to the nearest :00 or :30."""
    minute = 0 if dt.minute < 30 else 30
    return dt.replace(minute=minute, second=0, microsecond=0)


def is_slot_aligned(dt: datetime) -> bool:
    return dt.second == 0 and dt.microsecond == 0 and dt.minute in (0, 30)


SLOT = timedelta(minutes=30)
