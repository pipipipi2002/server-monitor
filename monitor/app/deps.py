"""FastAPI dependencies: shared SQLite connection and settings."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from threading import Lock

from fastapi import Depends

from app.config import Settings
from app.core.db import connect, init_schema


_conn: sqlite3.Connection | None = None
_conn_lock = Lock()


def _get_or_create_conn(path: Path) -> sqlite3.Connection:
    global _conn
    with _conn_lock:
        if _conn is None:
            _conn = connect(path)
            init_schema(_conn)
        return _conn


def get_settings() -> Settings:
    return Settings()


def get_db(settings: Settings = Depends(get_settings)) -> Iterator[sqlite3.Connection]:
    """Yield the shared SQLite connection. SQLite handles its own write locking."""
    yield _get_or_create_conn(Path(settings.db_path))


def reset_db_for_tests() -> None:
    """Tests use this to drop the cached connection between cases."""
    global _conn
    with _conn_lock:
        if _conn is not None:
            _conn.close()
        _conn = None
