"""Clock helpers — UTC ISO-8601 + slot rounding."""

from __future__ import annotations

from datetime import UTC, datetime


def test_now_iso_returns_utc_iso8601() -> None:
    from app.core.clock import now_iso

    s = now_iso()
    parsed = datetime.fromisoformat(s)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_parse_iso_utc_round_trip() -> None:
    from app.core.clock import now_iso, parse_iso

    s = now_iso()
    dt = parse_iso(s)
    assert dt.tzinfo == UTC


def test_floor_to_slot_aligns_to_30min_grid() -> None:
    from app.core.clock import floor_to_slot

    dt = datetime(2026, 5, 10, 14, 17, 4, tzinfo=UTC)
    assert floor_to_slot(dt) == datetime(2026, 5, 10, 14, 0, 0, tzinfo=UTC)

    dt = datetime(2026, 5, 10, 14, 47, 59, tzinfo=UTC)
    assert floor_to_slot(dt) == datetime(2026, 5, 10, 14, 30, 0, tzinfo=UTC)


def test_is_slot_aligned_recognizes_hour_and_half_hour() -> None:
    from app.core.clock import is_slot_aligned

    assert is_slot_aligned(datetime(2026, 5, 10, 14, 0, 0, tzinfo=UTC))
    assert is_slot_aligned(datetime(2026, 5, 10, 14, 30, 0, tzinfo=UTC))
    assert not is_slot_aligned(datetime(2026, 5, 10, 14, 15, 0, tzinfo=UTC))
    assert not is_slot_aligned(datetime(2026, 5, 10, 14, 0, 1, tzinfo=UTC))
