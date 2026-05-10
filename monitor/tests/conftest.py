"""Shared pytest fixtures for the monitor test suite."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A fresh SQLite file per test."""
    return tmp_path / "test.sqlite"


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """A sqlite3 connection with PRAGMAs and the schema applied.

    Imports are inside the fixture so that tests in Task 1.1 can drive
    schema creation by failing first.
    """
    from app.core.db import connect, init_schema

    c = connect(db_path)
    init_schema(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def fast_bcrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop bcrypt cost in tests so the suite stays snappy."""
    monkeypatch.setenv("BCRYPT_COST", "4")


# Late-2029 reference time so that any spec test date in 2030-01-* is unambiguously
# in the future and within the 7-day booking horizon. Using a fixed clock keeps
# tests deterministic and stops dates from rotting as wall-clock time advances.
FIXED_NOW = datetime(2029, 12, 31, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Pin app.core.clock.now and any module-level imports of it to FIXED_NOW.

    Modules that do `from app.core.clock import now` create their own binding,
    so we patch each known consumer in addition to the source module. New
    modules that import `now` should be added here.
    """
    monkeypatch.setattr("app.core.clock.now", lambda: FIXED_NOW)
    monkeypatch.setattr("app.core.clock.now_iso", lambda: FIXED_NOW.isoformat())
    for module_name in ("app.core.bookings", "app.core.aliases", "app.core.servers", "app.core.stale"):
        try:
            module = __import__(module_name, fromlist=["now"])
        except ImportError:
            continue
        if hasattr(module, "now"):
            monkeypatch.setattr(f"{module_name}.now", lambda: FIXED_NOW)
        if hasattr(module, "now_iso"):
            monkeypatch.setattr(f"{module_name}.now_iso", lambda: FIXED_NOW.isoformat())
    return FIXED_NOW


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    """Spin up the FastAPI app against a temp SQLite for a single test."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("BCRYPT_COST", "4")
    monkeypatch.setenv("ENROLLMENT_TOKEN_TTL", "3600")

    from app.deps import reset_db_for_tests
    from app.main import build_app

    reset_db_for_tests()
    app = build_app()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    reset_db_for_tests()
