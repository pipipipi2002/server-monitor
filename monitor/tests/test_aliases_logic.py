"""Public alias map — open read/write, no auth."""

from __future__ import annotations

import sqlite3


def test_upsert_inserts_and_then_updates(conn: sqlite3.Connection) -> None:
    from app.core.aliases import get_alias, upsert_alias

    upsert_alias(conn, device_name="DESKTOP-AB12C", alias="alice's laptop")
    assert get_alias(conn, "DESKTOP-AB12C") == "alice's laptop"
    upsert_alias(conn, device_name="DESKTOP-AB12C", alias="alice")
    assert get_alias(conn, "DESKTOP-AB12C") == "alice"


def test_upsert_strips_whitespace(conn: sqlite3.Connection) -> None:
    from app.core.aliases import get_alias, upsert_alias

    upsert_alias(conn, device_name="DESKTOP-AB12C", alias="  alice  ")
    assert get_alias(conn, "DESKTOP-AB12C") == "alice"


def test_upsert_rejects_empty_alias(conn: sqlite3.Connection) -> None:
    import pytest

    from app.core.aliases import upsert_alias

    with pytest.raises(ValueError):
        upsert_alias(conn, device_name="DESKTOP-AB12C", alias="   ")


def test_known_members_returns_distinct_aliases(conn: sqlite3.Connection) -> None:
    from app.core.aliases import known_members, upsert_alias

    upsert_alias(conn, device_name="A", alias="alice")
    upsert_alias(conn, device_name="A2", alias="alice")
    upsert_alias(conn, device_name="B", alias="bob")
    assert known_members(conn) == ["alice", "bob"]


def test_get_alias_returns_none_for_unknown_device(conn: sqlite3.Connection) -> None:
    from app.core.aliases import get_alias

    assert get_alias(conn, "GHOST") is None


def test_list_aliases_returns_all_with_metadata(conn: sqlite3.Connection) -> None:
    from app.core.aliases import list_aliases, upsert_alias

    upsert_alias(conn, device_name="A", alias="alice")
    upsert_alias(conn, device_name="B", alias="bob")
    rows = list_aliases(conn)
    assert {r["device_name"] for r in rows} == {"A", "B"}
    assert all("updated_at" in r.keys() for r in rows)
