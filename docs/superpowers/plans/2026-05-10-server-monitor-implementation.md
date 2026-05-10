# Server Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted server-monitor app — FastAPI + SQLite + Caddy in docker-compose, plus a cross-platform Python agent (PyInstaller-packaged for Windows Service / Linux systemd) that reports RDP/SSH session activity every ~5 s. Open web UI (no auth) with dashboard, public editable device-name aliases, and 30-min slot bookings (day view, 7-day horizon). Live updates via SSE. Single-command server onboarding. No secrets in repo.

**Architecture:** Two-process docker host (Caddy reverse proxy + FastAPI monitor with SQLite); one small Python agent per monitored server reporting via HTTPS bearer-token auth. Browser receives live diffs via SSE. Honor-system trust on the LAN — no end-user authentication.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLite (raw `sqlite3`), Jinja2 + HTMX + Alpine.js + Pico.css, `bcrypt`, `httpx`, `pytest` + `pytest-asyncio`, PyInstaller, Caddy, Docker / docker-compose.

**Conventions used throughout:**

- All timestamps in DB are stored as ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SS+00:00`); rendered in browser local time.
- Tests live under `<project>/tests/` and follow `test_<unit>.py` naming.
- Each task ends with a commit; commit messages follow `<type>(<area>): <subject>` (e.g. `feat(monitor): add booking conflict check`).
- For each step: the code shown is the **complete** content unless prefixed with a marker like `# ... existing code ... `.
- "Run X. Expected: Y" steps are required gates — do not advance if `Y` doesn't match.
- The dev box is Linux/WSL2; Windows-only code (`collect_windows.py`, `service_windows.py`, PyInstaller Windows build) is unit-tested with mocks here and **must be smoke-tested manually on a real Windows host** before release. That manual step is called out in Phase 8.

---

## Phase 0 — Project skeleton

### Task 0.1: Repo top-level scaffolding

**Files:**
- Create: `.env.example`
- Create: `pyproject.toml` (workspace root, pinned dev deps)
- Modify: `README.md` (already exists; only the "Local layout (planned)" section is fleshed out at the end of Phase 8)

- [ ] **Step 1: Write `.env.example`**

```bash
# .env.example — copy to .env (which is gitignored) and fill in
# All values shown are safe defaults; no real secrets live in this file.

# Public hostname agents will dial. Used by the install scripts and Caddy.
MONITOR_HOST=monitor.lan

# Display timezone for the dashboard. Storage is always UTC.
DISPLAY_TZ=UTC

# Bcrypt cost for hashing agent tokens. 10 = ~50ms; 4 in tests.
BCRYPT_COST=10

# How long an enrollment token stays valid (seconds).
ENROLLMENT_TOKEN_TTL=3600
```

- [ ] **Step 2: Write workspace `pyproject.toml`**

```toml
[project]
name = "server-monitor-workspace"
version = "0.0.0"
description = "Workspace root — pulls in monitor and agent in editable mode for tests."
requires-python = ">=3.12"

[tool.pytest.ini_options]
testpaths = ["monitor/tests", "agent/tests"]
asyncio_mode = "auto"
addopts = "-q"

[tool.ruff]
line-length = 100
target-version = "py312"
```

- [ ] **Step 3: Verify gitignore already excludes runtime state**

Run: `grep -E '^\.env$|^data/$|^\*\.sqlite' /home/marvinp/projects/server-monitor/.gitignore`
Expected: matches three lines (`.env`, `data/`, `*.sqlite*`).

- [ ] **Step 4: Commit**

```bash
git add .env.example pyproject.toml
git commit -m "chore: workspace scaffolding (env example, ruff/pytest config)"
```

### Task 0.2: Monitor package skeleton

**Files:**
- Create: `monitor/pyproject.toml`
- Create: `monitor/app/__init__.py` (empty)
- Create: `monitor/app/api/__init__.py` (empty)
- Create: `monitor/app/core/__init__.py` (empty)
- (Note: do NOT create `monitor/tests/__init__.py`. With pytest's `--import-mode=importlib` set in the workspace pyproject.toml, both `monitor/tests/` and `agent/tests/` need to remain non-package directories so they don't collide on the `tests.conftest` module name.)
- Create: `monitor/tests/conftest.py`

- [ ] **Step 1: Write `monitor/pyproject.toml`**

```toml
[project]
name = "server-monitor"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.110",
  "uvicorn[standard]>=0.29",
  "jinja2>=3.1",
  "pydantic>=2.6",
  "bcrypt>=4.1",
  "python-multipart>=0.0.9",  # form parsing
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-asyncio>=0.23",
  "httpx>=0.27",
  "ruff>=0.4",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["app*"]
```

- [ ] **Step 2: Create empty package init files**

```bash
touch monitor/app/__init__.py monitor/app/api/__init__.py monitor/app/core/__init__.py
```

- [ ] **Step 3: Write `monitor/tests/conftest.py`**

```python
"""Shared pytest fixtures for the monitor test suite."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest


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
    for module_name in ("app.core.bookings", "app.core.aliases", "app.core.servers"):
        try:
            module = __import__(module_name, fromlist=["now"])
        except ImportError:
            continue
        if hasattr(module, "now"):
            monkeypatch.setattr(f"{module_name}.now", lambda: FIXED_NOW)
        if hasattr(module, "now_iso"):
            monkeypatch.setattr(f"{module_name}.now_iso", lambda: FIXED_NOW.isoformat())
    return FIXED_NOW
```

Also add `from datetime import UTC, datetime` to the imports near the top of the file (after `from pathlib import Path`).

- [ ] **Step 4: Install dev deps in editable mode**

Run from `/home/marvinp/projects/server-monitor`:
```bash
python3 -m venv .venv && \
  .venv/bin/pip install -e 'monitor[dev]'
```
Expected: install completes; `.venv/bin/pytest --version` prints `pytest 8.x`.

- [ ] **Step 5: Verify pytest can discover the (empty) test tree**

Run: `.venv/bin/pytest monitor/tests -q`
Expected: `no tests ran` (exit 5, that's fine here) — the import path resolves.

- [ ] **Step 6: Commit**

```bash
git add monitor/
git commit -m "chore(monitor): package skeleton + pytest fixtures"
```

### Task 0.3: Agent package skeleton

**Files:**
- Create: `agent/pyproject.toml`
- Create: `agent/server_monitor_agent/__init__.py` (empty)
- (Note: do NOT create `agent/tests/__init__.py`; see same reasoning under Task 0.2.)
- Create: `agent/tests/conftest.py`

- [ ] **Step 1: Write `agent/pyproject.toml`**

```toml
[project]
name = "server-monitor-agent"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "httpx>=0.27",
  # Installed only on Windows targets via the PEP 508 marker; PyInstaller bundles it.
  "pywin32>=306; sys_platform == 'win32'",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pyinstaller>=6.0",
  "ruff>=0.4",
]

[project.scripts]
server-monitor-agent = "server_monitor_agent.__main__:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["server_monitor_agent*"]
```

- [ ] **Step 2: Create empty package init files**

```bash
touch agent/server_monitor_agent/__init__.py
```

- [ ] **Step 3: Write `agent/tests/conftest.py`**

```python
"""Shared pytest fixtures for the agent test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    """Per-test token file location."""
    p = tmp_path / "token"
    return p
```

- [ ] **Step 4: Install agent dev deps**

Run: `.venv/bin/pip install -e 'agent[dev]'`
Expected: install completes; `.venv/bin/pyinstaller --version` prints `6.x`.

- [ ] **Step 5: Commit**

```bash
git add agent/
git commit -m "chore(agent): package skeleton + dev deps"
```

---

## Phase 1 — Monitor core domain (TDD, no API)

### Task 1.1: SQLite schema and connection

**Files:**
- Create: `monitor/app/core/db.py`
- Create: `monitor/tests/test_db.py`

- [ ] **Step 1: Write the failing test `monitor/tests/test_db.py`**

```python
"""Schema bootstrapping and PRAGMA defaults."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def test_connect_enables_foreign_keys(db_path: Path) -> None:
    from app.core.db import connect

    c = connect(db_path)
    (fk,) = c.execute("PRAGMA foreign_keys").fetchone()
    assert fk == 1


def test_connect_uses_wal_mode(db_path: Path) -> None:
    from app.core.db import connect

    c = connect(db_path)
    (mode,) = c.execute("PRAGMA journal_mode").fetchone()
    assert mode == "wal"


def test_init_schema_creates_all_tables(db_path: Path) -> None:
    from app.core.db import connect, init_schema

    c = connect(db_path)
    init_schema(c)
    rows = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"servers", "sessions", "aliases", "bookings", "reports"}.issubset(names)


def test_init_schema_is_idempotent(db_path: Path) -> None:
    from app.core.db import connect, init_schema

    c = connect(db_path)
    init_schema(c)
    init_schema(c)  # must not raise


def test_servers_hostname_is_unique(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO servers (hostname, os, first_seen_at, last_seen_at) "
        "VALUES ('h1', 'linux', '2026-05-10T00:00:00+00:00', '2026-05-10T00:00:00+00:00')"
    )
    try:
        conn.execute(
            "INSERT INTO servers (hostname, os, first_seen_at, last_seen_at) "
            "VALUES ('h1', 'linux', '2026-05-10T00:00:00+00:00', '2026-05-10T00:00:00+00:00')"
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised


def test_bookings_unique_per_slot(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO servers (hostname, os, first_seen_at, last_seen_at) "
        "VALUES ('h1', 'linux', '2026-05-10T00:00:00+00:00', '2026-05-10T00:00:00+00:00')"
    )
    sid = conn.execute("SELECT id FROM servers").fetchone()[0]
    conn.execute(
        "INSERT INTO bookings (server_id, start_at, end_at, member_name, created_at) "
        "VALUES (?, '2026-05-10T14:00:00+00:00', '2026-05-10T14:30:00+00:00', 'alice', '2026-05-10T13:00:00+00:00')",
        (sid,),
    )
    try:
        conn.execute(
            "INSERT INTO bookings (server_id, start_at, end_at, member_name, created_at) "
            "VALUES (?, '2026-05-10T14:00:00+00:00', '2026-05-10T14:30:00+00:00', 'bob', '2026-05-10T13:00:00+00:00')",
            (sid,),
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.db'`.

- [ ] **Step 3: Write `monitor/app/core/db.py`**

```python
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
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_db.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add monitor/app/core/db.py monitor/tests/test_db.py
git commit -m "feat(monitor): SQLite schema + connection helpers"
```

### Task 1.2: Token generation and verification

**Files:**
- Create: `monitor/app/core/tokens.py`
- Create: `monitor/tests/test_tokens.py`

- [ ] **Step 1: Write `monitor/tests/test_tokens.py`**

```python
"""Bearer token generation and verification."""

from __future__ import annotations


def test_generate_token_is_high_entropy_url_safe() -> None:
    from app.core.tokens import generate_token

    a = generate_token()
    b = generate_token()
    assert a != b
    assert len(a) >= 32
    assert all(c.isalnum() or c in "-_" for c in a)


def test_hash_then_verify_succeeds() -> None:
    from app.core.tokens import generate_token, hash_token, verify_token

    t = generate_token()
    h = hash_token(t)
    assert verify_token(t, h) is True


def test_verify_rejects_wrong_token() -> None:
    from app.core.tokens import generate_token, hash_token, verify_token

    t = generate_token()
    h = hash_token(t)
    assert verify_token(generate_token(), h) is False


def test_verify_rejects_garbage_hash() -> None:
    from app.core.tokens import verify_token

    assert verify_token("anything", "not-a-bcrypt-hash") is False
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_tokens.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'app.core.tokens'`.

- [ ] **Step 3: Write `monitor/app/core/tokens.py`**

```python
"""Bearer token generation + bcrypt verification.

Tokens are 32 random bytes encoded as URL-safe base64 (≈ 256 bits).
Storing a bcrypt hash means the SQLite DB does not contain plaintext tokens.
"""

from __future__ import annotations

import os
import secrets

import bcrypt


def generate_token() -> str:
    """Return a fresh URL-safe random token."""
    return secrets.token_urlsafe(32)


def _cost() -> int:
    return int(os.environ.get("BCRYPT_COST", "10"))


def hash_token(token: str) -> str:
    return bcrypt.hashpw(token.encode("utf-8"), bcrypt.gensalt(rounds=_cost())).decode("ascii")


def verify_token(token: str, stored_hash: str) -> bool:
    try:
        return bcrypt.checkpw(token.encode("utf-8"), stored_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_tokens.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add monitor/app/core/tokens.py monitor/tests/test_tokens.py
git commit -m "feat(monitor): agent token generation and bcrypt verification"
```

### Task 1.3: Time helpers

**Files:**
- Create: `monitor/app/core/clock.py`
- Create: `monitor/tests/test_clock.py`

- [ ] **Step 1: Write `monitor/tests/test_clock.py`**

```python
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
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_clock.py -v`
Expected: FAIL `ModuleNotFoundError`.

- [ ] **Step 3: Write `monitor/app/core/clock.py`**

```python
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
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_clock.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add monitor/app/core/clock.py monitor/tests/test_clock.py
git commit -m "feat(monitor): clock helpers (UTC, slot alignment)"
```

### Task 1.4: Booking validation and conflict detection

**Files:**
- Create: `monitor/app/core/bookings.py`
- Create: `monitor/tests/test_bookings_logic.py`

- [ ] **Step 1: Write `monitor/tests/test_bookings_logic.py`**

```python
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
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_bookings_logic.py -v`
Expected: FAIL `ModuleNotFoundError`.

- [ ] **Step 3: Write `monitor/app/core/bookings.py`**

```python
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
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_bookings_logic.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add monitor/app/core/bookings.py monitor/tests/test_bookings_logic.py
git commit -m "feat(monitor): booking creation, deletion, day query"
```

### Task 1.5: Alias upsert and known-members query

**Files:**
- Create: `monitor/app/core/aliases.py`
- Create: `monitor/tests/test_aliases_logic.py`

- [ ] **Step 1: Write `monitor/tests/test_aliases_logic.py`**

```python
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
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_aliases_logic.py -v`
Expected: FAIL `ModuleNotFoundError`.

- [ ] **Step 3: Write `monitor/app/core/aliases.py`**

```python
"""Device-name → human-alias map. Public, no auth."""

from __future__ import annotations

import sqlite3

from app.core.clock import now_iso


def upsert_alias(conn: sqlite3.Connection, *, device_name: str, alias: str) -> None:
    cleaned = alias.strip()
    if not cleaned:
        raise ValueError("alias must not be empty")
    conn.execute(
        """
        INSERT INTO aliases (device_name, alias, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(device_name) DO UPDATE SET alias=excluded.alias, updated_at=excluded.updated_at
        """,
        (device_name, cleaned, now_iso()),
    )


def get_alias(conn: sqlite3.Connection, device_name: str) -> str | None:
    row = conn.execute("SELECT alias FROM aliases WHERE device_name=?", (device_name,)).fetchone()
    return row["alias"] if row else None


def list_aliases(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT device_name, alias, updated_at FROM aliases ORDER BY alias COLLATE NOCASE"
    ).fetchall()


def known_members(conn: sqlite3.Connection) -> list[str]:
    """Distinct alias values, sorted, for booking autocomplete."""
    rows = conn.execute(
        "SELECT DISTINCT alias FROM aliases ORDER BY alias COLLATE NOCASE"
    ).fetchall()
    return [r["alias"] for r in rows]
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_aliases_logic.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add monitor/app/core/aliases.py monitor/tests/test_aliases_logic.py
git commit -m "feat(monitor): alias upsert + known-members query"
```

### Task 1.6: Snapshot reconciliation (apply agent reports to `sessions`)

**Files:**
- Create: `monitor/app/core/sessions.py`
- Create: `monitor/tests/test_sessions_logic.py`

- [ ] **Step 1: Write `monitor/tests/test_sessions_logic.py`**

```python
"""Reconcile a snapshot from the agent into the sessions table."""

from __future__ import annotations

import sqlite3


def _seed_server(conn: sqlite3.Connection) -> int:
    conn.execute(
        "INSERT INTO servers (hostname, os, first_seen_at, last_seen_at) "
        "VALUES ('h1', 'linux', '2026-05-10T00:00:00+00:00', '2026-05-10T00:00:00+00:00')"
    )
    return conn.execute("SELECT id FROM servers").fetchone()[0]


def _make_snap(device: str, state: str = "active", proto: str = "rdp", logon: str = "2026-05-10T13:00:00+00:00"):
    return {
        "device_name": device,
        "username": "shared",
        "protocol": proto,
        "state": state,
        "logon_at": logon,
    }


def test_apply_inserts_new_sessions(conn: sqlite3.Connection) -> None:
    from app.core.sessions import apply_snapshot

    sid = _seed_server(conn)
    apply_snapshot(conn, server_id=sid, sessions=[_make_snap("LAPTOP-A")], received_at="2026-05-10T13:01:00+00:00")
    rows = conn.execute(
        "SELECT device_name, state, ended_at FROM sessions WHERE server_id=?", (sid,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["device_name"] == "LAPTOP-A"
    assert rows[0]["ended_at"] is None


def test_apply_keeps_existing_session_open_and_updates_last_seen(conn: sqlite3.Connection) -> None:
    from app.core.sessions import apply_snapshot

    sid = _seed_server(conn)
    apply_snapshot(conn, server_id=sid, sessions=[_make_snap("LAPTOP-A")], received_at="2026-05-10T13:01:00+00:00")
    apply_snapshot(conn, server_id=sid, sessions=[_make_snap("LAPTOP-A")], received_at="2026-05-10T13:05:00+00:00")
    rows = conn.execute(
        "SELECT id, last_seen_at, ended_at FROM sessions WHERE server_id=?", (sid,)
    ).fetchall()
    assert len(rows) == 1, "should not insert a duplicate row for the same logical session"
    assert rows[0]["ended_at"] is None
    assert rows[0]["last_seen_at"] == "2026-05-10T13:05:00+00:00"


def test_apply_marks_disappeared_session_as_ended(conn: sqlite3.Connection) -> None:
    from app.core.sessions import apply_snapshot

    sid = _seed_server(conn)
    apply_snapshot(conn, server_id=sid, sessions=[_make_snap("LAPTOP-A")], received_at="2026-05-10T13:01:00+00:00")
    apply_snapshot(conn, server_id=sid, sessions=[], received_at="2026-05-10T13:10:00+00:00")
    row = conn.execute("SELECT ended_at FROM sessions WHERE server_id=?", (sid,)).fetchone()
    assert row["ended_at"] == "2026-05-10T13:10:00+00:00"


def test_apply_state_change_updates_in_place(conn: sqlite3.Connection) -> None:
    from app.core.sessions import apply_snapshot

    sid = _seed_server(conn)
    apply_snapshot(
        conn, server_id=sid,
        sessions=[_make_snap("LAPTOP-A", state="active")],
        received_at="2026-05-10T13:01:00+00:00",
    )
    apply_snapshot(
        conn, server_id=sid,
        sessions=[_make_snap("LAPTOP-A", state="disconnected")],
        received_at="2026-05-10T13:05:00+00:00",
    )
    rows = conn.execute(
        "SELECT state FROM sessions WHERE server_id=? AND ended_at IS NULL", (sid,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["state"] == "disconnected"


def test_apply_returns_diff_summary(conn: sqlite3.Connection) -> None:
    from app.core.sessions import apply_snapshot

    sid = _seed_server(conn)
    diff = apply_snapshot(
        conn, server_id=sid,
        sessions=[_make_snap("A"), _make_snap("B")],
        received_at="2026-05-10T13:01:00+00:00",
    )
    assert sorted(d["device_name"] for d in diff.added) == ["A", "B"]
    assert diff.ended == [] and diff.changed == []

    diff = apply_snapshot(
        conn, server_id=sid,
        sessions=[_make_snap("A", state="disconnected")],
        received_at="2026-05-10T13:05:00+00:00",
    )
    assert [d["device_name"] for d in diff.changed] == ["A"]
    assert [d["device_name"] for d in diff.ended] == ["B"]
    assert diff.added == []


def test_list_active_sessions_returns_only_open(conn: sqlite3.Connection) -> None:
    from app.core.sessions import apply_snapshot, list_active_sessions

    sid = _seed_server(conn)
    apply_snapshot(conn, server_id=sid, sessions=[_make_snap("A"), _make_snap("B")], received_at="2026-05-10T13:01:00+00:00")
    apply_snapshot(conn, server_id=sid, sessions=[_make_snap("A")], received_at="2026-05-10T13:05:00+00:00")
    active = list_active_sessions(conn, server_id=sid)
    assert [a["device_name"] for a in active] == ["A"]
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_sessions_logic.py -v`
Expected: FAIL `ModuleNotFoundError`.

- [ ] **Step 3: Write `monitor/app/core/sessions.py`**

```python
"""Reconcile reported sessions against the live `sessions` table.

A "logical session" is identified by (server_id, device_name) while ended_at IS NULL.
- New device in snapshot → INSERT row.
- Existing device → UPDATE last_seen_at (and state if changed).
- Existing device missing from snapshot → mark ended_at = received_at.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import TypedDict


class SessionInput(TypedDict):
    device_name: str
    username: str | None
    protocol: str  # 'rdp' | 'ssh' | 'console'
    state: str     # 'active' | 'disconnected'
    logon_at: str  # ISO-8601 UTC


@dataclass
class Diff:
    added: list[dict] = field(default_factory=list)
    changed: list[dict] = field(default_factory=list)
    ended: list[dict] = field(default_factory=list)


def apply_snapshot(
    conn: sqlite3.Connection,
    *,
    server_id: int,
    sessions: list[SessionInput],
    received_at: str,
) -> Diff:
    diff = Diff()
    open_rows = conn.execute(
        "SELECT id, device_name, state FROM sessions WHERE server_id=? AND ended_at IS NULL",
        (server_id,),
    ).fetchall()
    open_by_device = {r["device_name"]: r for r in open_rows}
    seen: set[str] = set()

    for s in sessions:
        device = s["device_name"]
        seen.add(device)
        existing = open_by_device.get(device)
        if existing is None:
            conn.execute(
                """
                INSERT INTO sessions
                  (server_id, device_name, username, protocol, state, logon_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    server_id, device, s.get("username"), s["protocol"], s["state"],
                    s["logon_at"], received_at,
                ),
            )
            diff.added.append({"device_name": device, "state": s["state"], "logon_at": s["logon_at"]})
        else:
            if existing["state"] != s["state"]:
                conn.execute(
                    "UPDATE sessions SET state=?, last_seen_at=? WHERE id=?",
                    (s["state"], received_at, existing["id"]),
                )
                diff.changed.append({"device_name": device, "state": s["state"]})
            else:
                conn.execute(
                    "UPDATE sessions SET last_seen_at=? WHERE id=?",
                    (received_at, existing["id"]),
                )

    for device, row in open_by_device.items():
        if device not in seen:
            conn.execute(
                "UPDATE sessions SET ended_at=?, last_seen_at=? WHERE id=?",
                (received_at, received_at, row["id"]),
            )
            diff.ended.append({"device_name": device})

    conn.execute("UPDATE servers SET last_seen_at=? WHERE id=?", (received_at, server_id))
    return diff


def list_active_sessions(conn: sqlite3.Connection, *, server_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM sessions
        WHERE server_id=? AND ended_at IS NULL
        ORDER BY CASE state WHEN 'active' THEN 0 ELSE 1 END, logon_at
        """,
        (server_id,),
    ).fetchall()
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_sessions_logic.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add monitor/app/core/sessions.py monitor/tests/test_sessions_logic.py
git commit -m "feat(monitor): session reconciliation against agent snapshots"
```

### Task 1.7: Server enrollment / lookup helpers

**Files:**
- Create: `monitor/app/core/servers.py`
- Create: `monitor/tests/test_servers_logic.py`

- [ ] **Step 1: Write `monitor/tests/test_servers_logic.py`**

```python
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
    from app.core.servers import authenticate_agent, complete_enrollment, create_pending_server

    create_pending_server(conn, hostname="srv-a", os="linux", ttl_seconds=3600)
    enroll = conn.execute("SELECT enrollment_token FROM servers WHERE hostname='srv-a'").fetchone()[0]
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
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_servers_logic.py -v`
Expected: FAIL `ModuleNotFoundError`.

- [ ] **Step 3: Write `monitor/app/core/servers.py`**

```python
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
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_servers_logic.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add monitor/app/core/servers.py monitor/tests/test_servers_logic.py
git commit -m "feat(monitor): server enrollment + agent authentication"
```

---
## Phase 2 — Monitor API

### Task 2.1: App config + dependency wiring

**Files:**
- Create: `monitor/app/config.py`
- Create: `monitor/app/deps.py`
- Create: `monitor/tests/test_config.py`

- [ ] **Step 1: Write `monitor/tests/test_config.py`**

```python
"""Settings should pull from env with sane defaults."""

from __future__ import annotations

import pytest


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONITOR_HOST", raising=False)
    monkeypatch.delenv("DISPLAY_TZ", raising=False)
    monkeypatch.delenv("ENROLLMENT_TOKEN_TTL", raising=False)

    from app.config import Settings

    s = Settings()
    assert s.monitor_host == "monitor.lan"
    assert s.display_tz == "UTC"
    assert s.enrollment_token_ttl == 3600


def test_settings_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONITOR_HOST", "mon.example.com")
    monkeypatch.setenv("ENROLLMENT_TOKEN_TTL", "60")

    from app.config import Settings

    s = Settings()
    assert s.monitor_host == "mon.example.com"
    assert s.enrollment_token_ttl == 60
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_config.py -v`
Expected: FAIL `ModuleNotFoundError`.

- [ ] **Step 3: Write `monitor/app/config.py`**

```python
"""Process-wide settings, sourced from env vars."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    monitor_host: str = ""
    display_tz: str = ""
    enrollment_token_ttl: int = 0
    db_path: str = ""

    def __post_init__(self) -> None:
        self.monitor_host = os.environ.get("MONITOR_HOST", "monitor.lan")
        self.display_tz = os.environ.get("DISPLAY_TZ", "UTC")
        self.enrollment_token_ttl = int(os.environ.get("ENROLLMENT_TOKEN_TTL", "3600"))
        self.db_path = os.environ.get("DB_PATH", "/data/server-monitor.sqlite")
```

- [ ] **Step 4: Write `monitor/app/deps.py`**

```python
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
```

- [ ] **Step 5: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_config.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add monitor/app/config.py monitor/app/deps.py monitor/tests/test_config.py
git commit -m "feat(monitor): settings + dependency wiring"
```

### Task 2.2: SSE event broadcaster

**Files:**
- Create: `monitor/app/api/sse.py`
- Create: `monitor/tests/test_sse_broadcaster.py`

- [ ] **Step 1: Write `monitor/tests/test_sse_broadcaster.py`**

```python
"""In-process broadcaster used by the /sse endpoint."""

from __future__ import annotations

import asyncio


async def test_subscriber_receives_published_event() -> None:
    from app.api.sse import Broadcaster

    b = Broadcaster()
    queue = b.subscribe()
    await b.publish({"type": "session.added", "device_name": "A"})
    msg = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert msg["type"] == "session.added"


async def test_late_subscriber_does_not_get_old_events() -> None:
    from app.api.sse import Broadcaster

    b = Broadcaster()
    await b.publish({"type": "x"})
    queue = b.subscribe()
    try:
        await asyncio.wait_for(queue.get(), timeout=0.05)
        assert False, "should not receive past events"
    except TimeoutError:
        pass


async def test_unsubscribe_releases_queue() -> None:
    from app.api.sse import Broadcaster

    b = Broadcaster()
    q = b.subscribe()
    b.unsubscribe(q)
    await b.publish({"type": "x"})
    assert q.qsize() == 0


async def test_publish_is_robust_to_full_subscriber_queue() -> None:
    from app.api.sse import Broadcaster

    b = Broadcaster(queue_max=1)
    q = b.subscribe()
    await b.publish({"n": 1})
    await b.publish({"n": 2})  # must not block forever; drop-oldest keeps the newest
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    # drop-oldest semantics: queue retains the most-recent event
    assert any(d["n"] == 2 for d in out)
    assert len(out) <= 1
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_sse_broadcaster.py -v`
Expected: FAIL `ModuleNotFoundError`.

- [ ] **Step 3: Write `monitor/app/api/sse.py`**

```python
"""Server-sent event fan-out + endpoint."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse


class Broadcaster:
    """Fan-out queue: every subscriber gets every future event."""

    def __init__(self, queue_max: int = 64) -> None:
        self._subs: list[asyncio.Queue] = []
        self._queue_max = queue_max

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_max)
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subs.remove(q)
        except ValueError:
            pass

    async def publish(self, event: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer; drop the oldest to keep things flowing.
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except asyncio.QueueEmpty:
                    pass


broadcaster = Broadcaster()
router = APIRouter()


async def _stream(queue: asyncio.Queue) -> AsyncIterator[bytes]:
    try:
        # initial heartbeat so the client knows it's connected
        yield b": connected\n\n"
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield f"data: {json.dumps(event)}\n\n".encode("utf-8")
            except TimeoutError:
                yield b": ping\n\n"
    finally:
        broadcaster.unsubscribe(queue)


@router.get("/sse")
async def sse_endpoint() -> StreamingResponse:
    queue = broadcaster.subscribe()
    return StreamingResponse(_stream(queue), media_type="text/event-stream")
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_sse_broadcaster.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add monitor/app/api/sse.py monitor/tests/test_sse_broadcaster.py
git commit -m "feat(monitor): SSE broadcaster + /sse endpoint"
```

### Task 2.3: FastAPI app factory

**Files:**
- Create: `monitor/app/main.py`
- Create: `monitor/tests/conftest_app.py` (additional fixtures)
- Modify: `monitor/tests/conftest.py`

- [ ] **Step 1: Append a `client` fixture to `monitor/tests/conftest.py`**

Insert at the bottom of the existing file:
```python
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


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
```

- [ ] **Step 2: Write the failing test (smoke check that app boots)**

Create `monitor/tests/test_app_smoke.py`:
```python
async def test_app_responds_on_root(client) -> None:
    r = await client.get("/")
    assert r.status_code == 200
```

Run: `.venv/bin/pytest monitor/tests/test_app_smoke.py -v`
Expected: FAIL — `app.main` not present.

- [ ] **Step 3: Write `monitor/app/main.py`**

```python
"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.api import sse


def build_app() -> FastAPI:
    app = FastAPI(title="Server Monitor", version="0.1.0")
    app.include_router(sse.router)

    @app.get("/", response_class=HTMLResponse)
    async def _root() -> str:
        # Replaced in Phase 3 with the full template-rendered dashboard.
        return "<!doctype html><title>server-monitor</title><h1>server-monitor</h1>"

    return app


app = build_app()
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_app_smoke.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add monitor/app/main.py monitor/tests/conftest.py monitor/tests/test_app_smoke.py
git commit -m "feat(monitor): FastAPI app factory + test client fixture"
```

### Task 2.4: Agent enroll endpoint

**Files:**
- Create: `monitor/app/api/agents.py`
- Modify: `monitor/app/main.py` (register router)
- Create: `monitor/tests/test_api_enroll.py`

- [ ] **Step 1: Write `monitor/tests/test_api_enroll.py`**

```python
"""POST /api/enroll consumes a valid enrollment token and returns an agent token."""

from __future__ import annotations


async def _create_pending(client) -> tuple[str, str]:
    """Helper: create a pending server via the admin endpoint and return (hostname, enrollment_token)."""
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    assert r.status_code == 201, r.text
    return "srv-a", r.json()["enrollment_token"]


async def test_enroll_returns_agent_token(client) -> None:
    host, token = await _create_pending(client)
    r = await client.post(
        "/api/enroll",
        json={"hostname": host, "enrollment_token": token},
    )
    assert r.status_code == 200
    assert r.json()["agent_token"]


async def test_enroll_rejects_wrong_token(client) -> None:
    host, _ = await _create_pending(client)
    r = await client.post(
        "/api/enroll",
        json={"hostname": host, "enrollment_token": "bogus"},
    )
    assert r.status_code == 401


async def test_enroll_rejects_unknown_hostname(client) -> None:
    r = await client.post(
        "/api/enroll",
        json={"hostname": "ghost", "enrollment_token": "x"},
    )
    assert r.status_code == 401


async def test_enroll_is_one_shot(client) -> None:
    host, token = await _create_pending(client)
    ok = await client.post("/api/enroll", json={"hostname": host, "enrollment_token": token})
    assert ok.status_code == 200
    again = await client.post("/api/enroll", json={"hostname": host, "enrollment_token": token})
    assert again.status_code == 401
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_api_enroll.py -v`
Expected: FAIL — endpoints don't exist.

- [ ] **Step 3: Write `monitor/app/api/agents.py`**

```python
"""Agent-facing endpoints: admin pending-server creation, enroll, report.

Report ingestion is added in Task 2.5; this task covers admin + enroll only.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.config import Settings
from app.core.servers import EnrollmentError, complete_enrollment, create_pending_server
from app.deps import get_db, get_settings


router = APIRouter(prefix="/api")


class CreatePendingRequest(BaseModel):
    hostname: str = Field(min_length=1, max_length=255)
    os: str = Field(pattern="^(windows|linux)$")


class CreatePendingResponse(BaseModel):
    server_id: int
    enrollment_token: str


class EnrollRequest(BaseModel):
    hostname: str
    enrollment_token: str


class EnrollResponse(BaseModel):
    agent_token: str


@router.post(
    "/admin/server",
    response_model=CreatePendingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_pending(
    body: CreatePendingRequest,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CreatePendingResponse:
    sid, token = create_pending_server(
        conn, hostname=body.hostname, os=body.os, ttl_seconds=settings.enrollment_token_ttl
    )
    return CreatePendingResponse(server_id=sid, enrollment_token=token)


@router.post("/enroll", response_model=EnrollResponse)
def enroll(
    body: EnrollRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> EnrollResponse:
    try:
        token = complete_enrollment(
            conn, hostname=body.hostname, enrollment_token=body.enrollment_token
        )
    except EnrollmentError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    return EnrollResponse(agent_token=token)
```

- [ ] **Step 4: Wire the router into the app**

Edit `monitor/app/main.py`:
```python
"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.api import agents, sse


def build_app() -> FastAPI:
    app = FastAPI(title="Server Monitor", version="0.1.0")
    app.include_router(sse.router)
    app.include_router(agents.router)

    @app.get("/", response_class=HTMLResponse)
    async def _root() -> str:
        return "<!doctype html><title>server-monitor</title><h1>server-monitor</h1>"

    return app


app = build_app()
```

- [ ] **Step 5: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_api_enroll.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add monitor/app/api/agents.py monitor/app/main.py monitor/tests/test_api_enroll.py
git commit -m "feat(monitor): /api/enroll + admin pending-server endpoint"
```

### Task 2.5: Agent report endpoint

**Files:**
- Modify: `monitor/app/api/agents.py` (add `/api/report`)
- Create: `monitor/tests/test_api_report.py`

- [ ] **Step 1: Write `monitor/tests/test_api_report.py`**

```python
"""POST /api/report ingests an agent snapshot."""

from __future__ import annotations


async def _enroll(client) -> tuple[str, str]:
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    enroll_tok = r.json()["enrollment_token"]
    r = await client.post(
        "/api/enroll", json={"hostname": "srv-a", "enrollment_token": enroll_tok}
    )
    return "srv-a", r.json()["agent_token"]


async def test_report_accepts_valid_snapshot(client) -> None:
    host, token = await _enroll(client)
    r = await client.post(
        "/api/report",
        json={
            "hostname": host,
            "received_at": "2030-01-01T12:00:00+00:00",
            "sessions": [
                {
                    "device_name": "LAPTOP-A",
                    "username": "shared",
                    "protocol": "ssh",
                    "state": "active",
                    "logon_at": "2030-01-01T11:55:00+00:00",
                }
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True


async def test_report_rejects_missing_token(client) -> None:
    host, _ = await _enroll(client)
    r = await client.post(
        "/api/report",
        json={"hostname": host, "received_at": "x", "sessions": []},
    )
    assert r.status_code == 401


async def test_report_rejects_wrong_token(client) -> None:
    host, _ = await _enroll(client)
    r = await client.post(
        "/api/report",
        json={"hostname": host, "received_at": "2030-01-01T12:00:00+00:00", "sessions": []},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


async def test_report_publishes_sse_event(client) -> None:
    """When a report changes session state, an SSE event is published."""
    host, token = await _enroll(client)

    from app.api.sse import broadcaster

    queue = broadcaster.subscribe()
    try:
        r = await client.post(
            "/api/report",
            json={
                "hostname": host,
                "received_at": "2030-01-01T12:00:00+00:00",
                "sessions": [
                    {
                        "device_name": "LAPTOP-A",
                        "username": "shared",
                        "protocol": "rdp",
                        "state": "active",
                        "logon_at": "2030-01-01T11:55:00+00:00",
                    }
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        evt = queue.get_nowait()
        assert evt["type"] == "report"
        assert evt["hostname"] == host
    finally:
        broadcaster.unsubscribe(queue)
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_api_report.py -v`
Expected: FAIL.

- [ ] **Step 3: Extend `monitor/app/api/agents.py`**

Append to the existing file:
```python
import json
from typing import Annotated

from fastapi import Header

from app.api.sse import broadcaster
from app.core.clock import now_iso
from app.core.servers import authenticate_agent
from app.core.sessions import SessionInput, apply_snapshot


class ReportRequest(BaseModel):
    hostname: str
    received_at: str | None = None  # monitor stamps if absent
    sessions: list[dict]


def _auth(conn: sqlite3.Connection, hostname: str, header: str | None) -> int:
    if not header or not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = header.split(" ", 1)[1].strip()
    sid = authenticate_agent(conn, hostname=hostname, token=token)
    if sid is None:
        raise HTTPException(status_code=401, detail="invalid token")
    return sid


@router.post("/report")
async def report(
    body: ReportRequest,
    authorization: Annotated[str | None, Header()] = None,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    sid = _auth(conn, body.hostname, authorization)
    received_at = body.received_at or now_iso()
    sessions: list[SessionInput] = [
        {
            "device_name": s["device_name"],
            "username": s.get("username"),
            "protocol": s["protocol"],
            "state": s["state"],
            "logon_at": s["logon_at"],
        }
        for s in body.sessions
    ]
    diff = apply_snapshot(conn, server_id=sid, sessions=sessions, received_at=received_at)
    conn.execute(
        "INSERT INTO reports (server_id, received_at, payload_json) VALUES (?, ?, ?)",
        (sid, received_at, json.dumps({"sessions": body.sessions})),
    )
    await broadcaster.publish(
        {
            "type": "report",
            "hostname": body.hostname,
            "added": diff.added,
            "changed": diff.changed,
            "ended": diff.ended,
            "received_at": received_at,
        }
    )
    return {"accepted": True}
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_api_report.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add monitor/app/api/agents.py monitor/tests/test_api_report.py
git commit -m "feat(monitor): /api/report ingestion + SSE broadcast"
```

### Task 2.6: Booking endpoints

**Files:**
- Create: `monitor/app/api/bookings.py`
- Modify: `monitor/app/main.py` (register router)
- Create: `monitor/tests/test_api_bookings.py`

- [ ] **Step 1: Write `monitor/tests/test_api_bookings.py`**

```python
"""POST /bookings, DELETE /bookings/<id>, listing per server+day."""

from __future__ import annotations


async def _server(client) -> int:
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    return r.json()["server_id"]


async def test_create_booking_returns_201_with_id(client) -> None:
    sid = await _server(client)
    r = await client.post(
        "/bookings",
        json={
            "server_id": sid,
            "start_at": "2030-01-01T14:00:00+00:00",
            "member_name": "alice",
        },
    )
    assert r.status_code == 201
    assert r.json()["id"]


async def test_duplicate_booking_returns_409(client) -> None:
    sid = await _server(client)
    payload = {"server_id": sid, "start_at": "2030-01-01T14:00:00+00:00", "member_name": "alice"}
    await client.post("/bookings", json=payload)
    again = await client.post("/bookings", json={**payload, "member_name": "bob"})
    assert again.status_code == 409


async def test_invalid_slot_returns_422(client) -> None:
    sid = await _server(client)
    r = await client.post(
        "/bookings",
        json={"server_id": sid, "start_at": "2030-01-01T14:15:00+00:00", "member_name": "alice"},
    )
    assert r.status_code == 422


async def test_list_bookings_for_day(client) -> None:
    sid = await _server(client)
    await client.post(
        "/bookings",
        json={"server_id": sid, "start_at": "2030-01-01T14:00:00+00:00", "member_name": "alice"},
    )
    await client.post(
        "/bookings",
        json={"server_id": sid, "start_at": "2030-01-02T14:00:00+00:00", "member_name": "bob"},
    )
    r = await client.get(f"/bookings?server_id={sid}&day=2030-01-01")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["member_name"] == "alice"


async def test_delete_booking(client) -> None:
    sid = await _server(client)
    r = await client.post(
        "/bookings",
        json={"server_id": sid, "start_at": "2030-01-01T14:00:00+00:00", "member_name": "alice"},
    )
    bid = r.json()["id"]
    rd = await client.delete(f"/bookings/{bid}")
    assert rd.status_code == 204
    rd2 = await client.delete(f"/bookings/{bid}")
    assert rd2.status_code == 404
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_api_bookings.py -v`
Expected: FAIL.

- [ ] **Step 3: Write `monitor/app/api/bookings.py`**

```python
"""Bookings API: create, list-for-day, delete. No auth (open on the LAN)."""

from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.api.sse import broadcaster
from app.core.bookings import (
    BookingConflict,
    BookingError,
    create_booking,
    delete_booking,
    list_bookings_for_day,
)
from app.core.clock import parse_iso
from app.deps import get_db


router = APIRouter()


class CreateBookingRequest(BaseModel):
    server_id: int
    start_at: str
    member_name: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=200)


@router.post("/bookings", status_code=status.HTTP_201_CREATED)
async def create(
    body: CreateBookingRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    start = parse_iso(body.start_at)
    try:
        bid = create_booking(
            conn,
            server_id=body.server_id,
            start_at=start,
            member_name=body.member_name,
            note=body.note,
        )
    except BookingConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except BookingError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    await broadcaster.publish(
        {
            "type": "booking.created",
            "id": bid,
            "server_id": body.server_id,
            "start_at": start.isoformat(),
            "member_name": body.member_name,
        }
    )
    return {"id": bid}


@router.get("/bookings")
def list_for_day(
    server_id: int = Query(...),
    day: str = Query(..., description="YYYY-MM-DD"),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        d = date.fromisoformat(day)
    except ValueError as e:
        raise HTTPException(status_code=422, detail="invalid day") from e
    rows = list_bookings_for_day(conn, server_id=server_id, day=d)
    return {"items": [dict(r) for r in rows]}


@router.delete("/bookings/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    booking_id: int,
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    if not delete_booking(conn, booking_id):
        raise HTTPException(status_code=404, detail="not found")
    await broadcaster.publish({"type": "booking.deleted", "id": booking_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 4: Wire router; edit `monitor/app/main.py`**

Replace the imports + body of `build_app`:
```python
from app.api import agents, bookings, sse


def build_app() -> FastAPI:
    app = FastAPI(title="Server Monitor", version="0.1.0")
    app.include_router(sse.router)
    app.include_router(agents.router)
    app.include_router(bookings.router)

    @app.get("/", response_class=HTMLResponse)
    async def _root() -> str:
        return "<!doctype html><title>server-monitor</title><h1>server-monitor</h1>"

    return app
```

- [ ] **Step 5: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_api_bookings.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add monitor/app/api/bookings.py monitor/app/main.py monitor/tests/test_api_bookings.py
git commit -m "feat(monitor): bookings CRUD endpoints"
```

### Task 2.7: Alias endpoints

**Files:**
- Create: `monitor/app/api/aliases.py`
- Modify: `monitor/app/main.py`
- Create: `monitor/tests/test_api_aliases.py`

- [ ] **Step 1: Write `monitor/tests/test_api_aliases.py`**

```python
async def test_upsert_alias_then_list(client) -> None:
    r = await client.post(
        "/aliases", json={"device_name": "DESKTOP-AB12C", "alias": "alice"}
    )
    assert r.status_code == 200
    rl = await client.get("/aliases")
    items = rl.json()["items"]
    assert any(i["device_name"] == "DESKTOP-AB12C" and i["alias"] == "alice" for i in items)


async def test_known_members_list(client) -> None:
    await client.post("/aliases", json={"device_name": "A", "alias": "alice"})
    await client.post("/aliases", json={"device_name": "B", "alias": "bob"})
    r = await client.get("/aliases/members")
    assert r.json()["members"] == ["alice", "bob"]


async def test_empty_alias_returns_422(client) -> None:
    r = await client.post("/aliases", json={"device_name": "A", "alias": "   "})
    assert r.status_code == 422


async def test_alias_change_publishes_sse(client) -> None:
    from app.api.sse import broadcaster

    q = broadcaster.subscribe()
    try:
        await client.post("/aliases", json={"device_name": "A", "alias": "alice"})
        evt = q.get_nowait()
        assert evt["type"] == "alias.updated"
        assert evt["device_name"] == "A"
        assert evt["alias"] == "alice"
    finally:
        broadcaster.unsubscribe(q)
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_api_aliases.py -v`
Expected: FAIL.

- [ ] **Step 3: Write `monitor/app/api/aliases.py`**

```python
"""Public, editable alias map. No auth."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.sse import broadcaster
from app.core.aliases import known_members, list_aliases, upsert_alias
from app.deps import get_db


router = APIRouter()


class UpsertAliasRequest(BaseModel):
    device_name: str = Field(min_length=1, max_length=255)
    alias: str = Field(min_length=1, max_length=64)


@router.post("/aliases")
async def upsert(
    body: UpsertAliasRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        upsert_alias(conn, device_name=body.device_name, alias=body.alias)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    cleaned = body.alias.strip()
    await broadcaster.publish(
        {"type": "alias.updated", "device_name": body.device_name, "alias": cleaned}
    )
    return {"device_name": body.device_name, "alias": cleaned}


@router.get("/aliases")
def list_all(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return {"items": [dict(r) for r in list_aliases(conn)]}


@router.get("/aliases/members")
def members(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return {"members": known_members(conn)}
```

- [ ] **Step 4: Wire router into `monitor/app/main.py`**

Add import `from app.api import aliases` and `app.include_router(aliases.router)` in `build_app()`.

- [ ] **Step 5: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_api_aliases.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add monitor/app/api/aliases.py monitor/app/main.py monitor/tests/test_api_aliases.py
git commit -m "feat(monitor): alias upsert/list/members endpoints"
```

### Task 2.8: Stale-server background task

**Files:**
- Create: `monitor/app/core/stale.py`
- Modify: `monitor/app/main.py` (register lifespan)
- Create: `monitor/tests/test_stale.py`

- [ ] **Step 1: Write `monitor/tests/test_stale.py`**

```python
"""Stale-server detection: any server unseen for > threshold goes 'offline'."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta


def test_check_stale_emits_offline_for_old_servers(conn: sqlite3.Connection) -> None:
    from app.core.stale import check_stale

    long_ago = (datetime.now(UTC) - timedelta(seconds=90)).isoformat()
    fresh = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO servers (hostname, os, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)",
        ("old", "linux", long_ago, long_ago),
    )
    conn.execute(
        "INSERT INTO servers (hostname, os, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)",
        ("new", "linux", fresh, fresh),
    )
    events = check_stale(conn, threshold_seconds=60)
    hosts = {e["hostname"] for e in events}
    assert "old" in hosts
    assert "new" not in hosts
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_stale.py -v`
Expected: FAIL.

- [ ] **Step 3: Write `monitor/app/core/stale.py`**

```python
"""Detect servers whose agents haven't reported recently."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from app.core.clock import now, parse_iso


def check_stale(conn: sqlite3.Connection, *, threshold_seconds: int) -> list[dict]:
    cutoff = now() - timedelta(seconds=threshold_seconds)
    rows = conn.execute("SELECT id, hostname, last_seen_at FROM servers").fetchall()
    return [
        {"server_id": r["id"], "hostname": r["hostname"], "last_seen_at": r["last_seen_at"]}
        for r in rows
        if parse_iso(r["last_seen_at"]) < cutoff
    ]
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_stale.py -v`
Expected: 1 passed.

- [ ] **Step 5: Wire a lifespan task into `monitor/app/main.py`**

Replace contents:
```python
"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.api import agents, aliases, bookings, sse
from app.api.sse import broadcaster
from app.core.stale import check_stale
from app.deps import get_settings, _get_or_create_conn
from pathlib import Path


_seen_offline: set[int] = set()


async def _stale_loop() -> None:
    while True:
        try:
            settings = get_settings()
            conn = _get_or_create_conn(Path(settings.db_path))
            stale = check_stale(conn, threshold_seconds=60)
            current = {s["server_id"] for s in stale}
            # newly-offline
            for s in stale:
                if s["server_id"] not in _seen_offline:
                    await broadcaster.publish({"type": "server.offline", **s})
            # newly-online
            for sid in list(_seen_offline):
                if sid not in current:
                    await broadcaster.publish({"type": "server.online", "server_id": sid})
            _seen_offline.clear()
            _seen_offline.update(current)
        except Exception:  # noqa: BLE001
            # Background loop must never die.
            pass
        await asyncio.sleep(30)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    task = asyncio.create_task(_stale_loop())
    try:
        yield
    finally:
        task.cancel()


def build_app() -> FastAPI:
    app = FastAPI(title="Server Monitor", version="0.1.0", lifespan=_lifespan)
    app.include_router(sse.router)
    app.include_router(agents.router)
    app.include_router(bookings.router)
    app.include_router(aliases.router)

    @app.get("/", response_class=HTMLResponse)
    async def _root() -> str:
        return "<!doctype html><title>server-monitor</title><h1>server-monitor</h1>"

    return app


app = build_app()
```

- [ ] **Step 6: Re-run the full monitor test suite**

Run: `.venv/bin/pytest monitor/tests -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add monitor/app/core/stale.py monitor/app/main.py monitor/tests/test_stale.py
git commit -m "feat(monitor): stale-server detection background loop"
```

### Task 2.9: Agent binary serving endpoint (placeholder until Task 5.3 produces real binaries)

**Files:**
- Modify: `monitor/app/api/agents.py`
- Create: `monitor/tests/test_api_agent_binary.py`

- [ ] **Step 1: Write `monitor/tests/test_api_agent_binary.py`**

```python
"""GET /api/agent-binary serves an OS-appropriate file from /agents-dist."""

from __future__ import annotations

from pathlib import Path


async def test_serves_linux_binary_when_present(client, tmp_path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "agent-linux-x86_64").write_bytes(b"#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("AGENT_DIST_DIR", str(dist))

    r = await client.get("/api/agent-binary?os=linux&arch=x86_64")
    assert r.status_code == 200
    assert r.content == b"#!/bin/sh\nexit 0\n"


async def test_returns_404_when_binary_missing(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DIST_DIR", str(tmp_path / "empty"))
    r = await client.get("/api/agent-binary?os=linux&arch=x86_64")
    assert r.status_code == 404
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_api_agent_binary.py -v`
Expected: FAIL.

- [ ] **Step 3: Append to `monitor/app/api/agents.py`**

```python
import os as _os
from pathlib import Path

from fastapi.responses import FileResponse


def _binary_name(os_: str, arch: str) -> str:
    if os_ == "windows":
        return "agent-windows.exe"
    if os_ == "linux":
        return f"agent-linux-{arch}"
    raise HTTPException(status_code=400, detail="unsupported os")


@router.get("/agent-binary")
def agent_binary(os: str, arch: str = "x86_64") -> FileResponse:
    dist = Path(_os.environ.get("AGENT_DIST_DIR", "/agents-dist"))
    name = _binary_name(os, arch)
    p = dist / name
    if not p.exists():
        raise HTTPException(status_code=404, detail="binary not built yet")
    return FileResponse(p, filename=name, media_type="application/octet-stream")
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_api_agent_binary.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add monitor/app/api/agents.py monitor/tests/test_api_agent_binary.py
git commit -m "feat(monitor): /api/agent-binary serves built artifacts"
```

---
## Phase 3 — Monitor Web UI

Templates use server-rendered Jinja2 + HTMX for dynamic swaps and Alpine.js for tiny client-state needs. CSS is Pico.css (single CDN file) plus a small `app.css`.

### Task 3.1: Base layout, static assets, web router skeleton

**Files:**
- Create: `monitor/app/api/web.py`
- Create: `monitor/app/templates/base.html`
- Create: `monitor/app/templates/_partials/sse.html`
- Create: `monitor/app/static/app.css`
- Create: `monitor/app/static/app.js`
- Modify: `monitor/app/main.py` (mount static, register web router)
- Modify: `monitor/pyproject.toml` (include templates/static in package data)
- Create: `monitor/tests/test_web_smoke.py`

- [ ] **Step 1: Write the failing smoke test `monitor/tests/test_web_smoke.py`**

```python
"""Web router serves the base layout and static assets."""

from __future__ import annotations


async def test_root_renders_html_with_title(client) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "<title>" in body
    assert "Server Monitor" in body


async def test_static_app_css_served(client) -> None:
    r = await client.get("/static/app.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]


async def test_root_includes_sse_subscriber_script(client) -> None:
    r = await client.get("/")
    assert "/sse" in r.text  # the SSE wiring partial is included
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_web_smoke.py -v`
Expected: FAIL.

- [ ] **Step 3: Update `monitor/pyproject.toml` to include templates and static files**

Replace the `[tool.setuptools.packages.find]` block with:
```toml
[tool.setuptools.packages.find]
include = ["app*"]

[tool.setuptools.package-data]
app = ["templates/**/*.html", "static/**/*"]
```

- [ ] **Step 4: Write `monitor/app/templates/base.html`**

```html
<!doctype html>
<html lang="en" data-tz="{{ display_tz }}">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{% block title %}Server Monitor{% endblock %}</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
    <link rel="stylesheet" href="/static/app.css">
    <script defer src="https://unpkg.com/alpinejs@3.13.10/dist/cdn.min.js"></script>
    <script src="https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js"></script>
</head>
<body>
    <header class="container">
        <nav>
            <ul>
                <li><strong><a href="/" class="contrast">Server Monitor</a></strong></li>
            </ul>
            <ul>
                <li><a href="/aliases">Aliases</a></li>
                <li><a href="/enroll">Add server</a></li>
            </ul>
        </nav>
    </header>
    <main class="container">
        {% block content %}{% endblock %}
    </main>
    {% include "_partials/sse.html" %}
    <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 5: Write `monitor/app/templates/_partials/sse.html`**

```html
<script>
    // Subscribe to SSE and dispatch DOM events that templates listen for.
    (function () {
        if (!window.EventSource) return;
        const es = new EventSource("/sse");
        es.onmessage = function (e) {
            try {
                const evt = JSON.parse(e.data);
                window.dispatchEvent(new CustomEvent("sm:event", { detail: evt }));
            } catch (_) { /* ignore */ }
        };
    })();
</script>
```

- [ ] **Step 6: Write `monitor/app/static/app.css`**

```css
:root { --sm-card-min: 280px; }

.sm-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(var(--sm-card-min), 1fr));
    gap: 1rem;
}

.sm-card { padding: 1rem; border: 1px solid var(--pico-muted-border-color); border-radius: 0.5rem; }
.sm-card h3 { margin: 0 0 0.25rem 0; }

.sm-badge { display: inline-block; padding: 0.05rem 0.4rem; border-radius: 0.5rem; font-size: 0.75rem; }
.sm-badge--online { background: #16a34a22; color: #16a34a; }
.sm-badge--offline { background: #dc262622; color: #dc2626; }
.sm-badge--active { background: #2563eb22; color: #2563eb; }
.sm-badge--disconnected { background: #ca8a0422; color: #ca8a04; }

.sm-day-grid {
    display: grid;
    grid-template-columns: 6ch 1fr;
    gap: 0.25rem;
    align-items: center;
}
.sm-slot {
    display: block;
    padding: 0.25rem 0.5rem;
    border: 1px dashed var(--pico-muted-border-color);
    border-radius: 0.25rem;
    cursor: pointer;
    text-align: left;
    background: transparent;
}
.sm-slot--booked {
    background: var(--pico-primary-background);
    color: var(--pico-primary-inverse);
    cursor: default;
    border-style: solid;
}
.sm-slot--past { opacity: 0.5; }

.sm-modal[hidden] { display: none; }
.sm-modal {
    position: fixed; inset: 0; background: #0008; display: grid; place-items: center;
}
.sm-modal__panel {
    background: var(--pico-background-color); padding: 1rem;
    border-radius: 0.5rem; min-width: 320px;
}
```

- [ ] **Step 7: Write `monitor/app/static/app.js`**

```javascript
// Convert `data-iso` timestamps to relative-local strings on render and after SSE updates.
function relativeLocal(iso) {
    if (!iso) return "";
    const dt = new Date(iso);
    const diff = Math.round((Date.now() - dt.getTime()) / 1000);
    if (diff < 60) return diff + "s ago";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    return Math.floor(diff / 3600) + "h ago";
}
function applyRelative(scope) {
    (scope || document).querySelectorAll("[data-iso]").forEach(el => {
        el.textContent = relativeLocal(el.getAttribute("data-iso"));
    });
}
document.addEventListener("DOMContentLoaded", () => applyRelative());
document.addEventListener("htmx:afterSwap", e => applyRelative(e.target));
setInterval(() => applyRelative(), 30 * 1000);
```

- [ ] **Step 8: Write `monitor/app/api/web.py`**

```python
"""Server-rendered web pages."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.deps import get_db, get_settings


_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def templates() -> Jinja2Templates:
    return _TEMPLATES


router = APIRouter(default_response_class=HTMLResponse)


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return _TEMPLATES.TemplateResponse(
        request,
        "dashboard.html",
        {"display_tz": settings.display_tz, "servers": []},
    )
```

- [ ] **Step 9: Write a placeholder `monitor/app/templates/dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}Dashboard — Server Monitor{% endblock %}
{% block content %}
<h2>Servers</h2>
<div class="sm-grid" id="server-grid">
    {% if not servers %}
        <p>No servers yet. <a href="/enroll">Add one</a>.</p>
    {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 10: Mount static, register web router; replace `monitor/app/main.py`**

```python
"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import agents, aliases, bookings, sse, web
from app.api.sse import broadcaster
from app.core.stale import check_stale
from app.deps import _get_or_create_conn, get_settings


_seen_offline: set[int] = set()


async def _stale_loop() -> None:
    while True:
        try:
            settings = get_settings()
            conn = _get_or_create_conn(Path(settings.db_path))
            stale = check_stale(conn, threshold_seconds=60)
            current = {s["server_id"] for s in stale}
            for s in stale:
                if s["server_id"] not in _seen_offline:
                    await broadcaster.publish({"type": "server.offline", **s})
            for sid in list(_seen_offline):
                if sid not in current:
                    await broadcaster.publish({"type": "server.online", "server_id": sid})
            _seen_offline.clear()
            _seen_offline.update(current)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(30)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    task = asyncio.create_task(_stale_loop())
    try:
        yield
    finally:
        task.cancel()


def build_app() -> FastAPI:
    app = FastAPI(title="Server Monitor", version="0.1.0", lifespan=_lifespan)

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(sse.router)
    app.include_router(agents.router)
    app.include_router(bookings.router)
    app.include_router(aliases.router)
    app.include_router(web.router)
    return app


app = build_app()
```

- [ ] **Step 11: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_web_smoke.py -v` and the existing smoke test:
```bash
.venv/bin/pytest monitor/tests/test_app_smoke.py -v
```
Expected: previous smoke now fails on body content. Update `monitor/tests/test_app_smoke.py`:

```python
async def test_app_responds_on_root(client) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text
```

- [ ] **Step 12: Re-run all monitor tests**

Run: `.venv/bin/pytest monitor/tests -q`
Expected: all green.

- [ ] **Step 13: Commit**

```bash
git add monitor/app/api/web.py monitor/app/templates/ monitor/app/static/ \
        monitor/app/main.py monitor/pyproject.toml monitor/tests/test_web_smoke.py \
        monitor/tests/test_app_smoke.py
git commit -m "feat(monitor): web router, base layout, static assets, SSE wiring"
```

### Task 3.2: Dashboard cards (full)

**Files:**
- Modify: `monitor/app/api/web.py`
- Modify: `monitor/app/templates/dashboard.html`
- Create: `monitor/app/templates/_partials/server_card.html`
- Create: `monitor/tests/test_web_dashboard.py`

- [ ] **Step 1: Write `monitor/tests/test_web_dashboard.py`**

```python
async def test_dashboard_lists_enrolled_server(client) -> None:
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    enroll = r.json()["enrollment_token"]
    await client.post("/api/enroll", json={"hostname": "srv-a", "enrollment_token": enroll})

    body = (await client.get("/")).text
    assert "srv-a" in body
    assert "linux" in body
    assert "agent offline" in body.lower() or "online" in body.lower()


async def test_dashboard_shows_session_with_alias(client) -> None:
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    enroll = r.json()["enrollment_token"]
    er = await client.post("/api/enroll", json={"hostname": "srv-a", "enrollment_token": enroll})
    token = er.json()["agent_token"]

    await client.post("/aliases", json={"device_name": "LAPTOP-A", "alias": "alice's laptop"})
    await client.post(
        "/api/report",
        json={
            "hostname": "srv-a",
            "received_at": "2030-01-01T12:00:00+00:00",
            "sessions": [{
                "device_name": "LAPTOP-A",
                "username": "shared",
                "protocol": "ssh",
                "state": "active",
                "logon_at": "2030-01-01T11:55:00+00:00",
            }],
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    body = (await client.get("/")).text
    # Jinja2 autoescape (which we keep on for XSS safety) renders an apostrophe as &#39;
    assert "alice&#39;s laptop" in body or "alice's laptop" in body
    assert "active" in body.lower()
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_web_dashboard.py -v`
Expected: FAIL — current dashboard shows no servers.

- [ ] **Step 3: Replace `monitor/app/api/web.py`**

```python
"""Server-rendered web pages."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.core.aliases import get_alias
from app.core.clock import now, parse_iso
from app.core.servers import list_servers
from app.core.sessions import list_active_sessions
from app.deps import get_db, get_settings


_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


def _server_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    sessions = list_active_sessions(conn, server_id=row["id"])
    enriched = []
    for s in sessions:
        enriched.append(
            {
                "device_name": s["device_name"],
                "alias": get_alias(conn, s["device_name"]),
                "state": s["state"],
                "logon_at": s["logon_at"],
                "protocol": s["protocol"],
                "username": s["username"],
            }
        )
    online = parse_iso(row["last_seen_at"]) >= now() - timedelta(seconds=60) if row["last_seen_at"] else False
    return {
        "id": row["id"],
        "hostname": row["hostname"],
        "os": row["os"],
        "online": online,
        "enrolled": bool(row["enrolled"]),
        "last_seen_at": row["last_seen_at"],
        "sessions": enriched,
    }


router = APIRouter(default_response_class=HTMLResponse)


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    servers = [_server_view(conn, r) for r in list_servers(conn)]
    return _TEMPLATES.TemplateResponse(
        request,
        "dashboard.html",
        {"display_tz": settings.display_tz, "servers": servers},
    )
```

- [ ] **Step 4: Replace `monitor/app/templates/dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}Dashboard — Server Monitor{% endblock %}
{% block content %}
<h2>Servers</h2>
<div class="sm-grid" id="server-grid">
    {% for s in servers %}
        {% include "_partials/server_card.html" %}
    {% else %}
        <p>No servers yet. <a href="/enroll">Add one</a>.</p>
    {% endfor %}
</div>
{% endblock %}
```

- [ ] **Step 5: Write `monitor/app/templates/_partials/server_card.html`**

```html
<article class="sm-card" id="server-{{ s.id }}">
    <header>
        <h3>
            <a href="/server/{{ s.id }}">{{ s.hostname }}</a>
            <small>({{ s.os }})</small>
        </h3>
        {% if not s.enrolled %}
            <span class="sm-badge sm-badge--offline">pending enroll</span>
        {% elif s.online %}
            <span class="sm-badge sm-badge--online">agent online</span>
        {% else %}
            <span class="sm-badge sm-badge--offline">agent offline</span>
        {% endif %}
    </header>

    {% if s.sessions %}
        <ul>
            {% for sess in s.sessions %}
                <li>
                    <strong>{{ sess.alias or sess.device_name }}</strong>
                    {% if sess.alias %}<small>({{ sess.device_name }})</small>{% endif %}
                    <span class="sm-badge sm-badge--{{ sess.state }}">{{ sess.state }}</span>
                    <small><span data-iso="{{ sess.logon_at }}"></span></small>
                </li>
            {% endfor %}
        </ul>
    {% else %}
        <p><em>No active sessions.</em></p>
    {% endif %}
</article>
```

- [ ] **Step 6: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_web_dashboard.py -v`
Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add monitor/app/api/web.py monitor/app/templates/dashboard.html \
        monitor/app/templates/_partials/server_card.html monitor/tests/test_web_dashboard.py
git commit -m "feat(monitor): dashboard with live server cards"
```

### Task 3.3: Aliases page (inline edit)

**Files:**
- Modify: `monitor/app/api/web.py`
- Create: `monitor/app/templates/aliases.html`
- Create: `monitor/app/templates/_partials/alias_row.html`
- Modify: `monitor/app/api/aliases.py` (return HTML partial when client wants HTMX)
- Create: `monitor/tests/test_web_aliases.py`

- [ ] **Step 1: Write `monitor/tests/test_web_aliases.py`**

```python
async def test_aliases_page_lists_existing(client) -> None:
    await client.post("/aliases", json={"device_name": "DESKTOP-A", "alias": "alice"})
    body = (await client.get("/aliases")).text
    assert "DESKTOP-A" in body
    assert "alice" in body
    assert "<form" in body  # there's an inline edit form


async def test_aliases_post_form_redirects_back(client) -> None:
    r = await client.post(
        "/aliases",
        data={"device_name": "DESKTOP-B", "alias": "bob"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    # form post returns the updated row partial (200) for HTMX, OR 303 for plain submit.
    assert r.status_code in (200, 303)
    body = (await client.get("/aliases")).text
    assert "DESKTOP-B" in body
    assert "bob" in body
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_web_aliases.py -v`
Expected: FAIL — `/aliases` GET currently returns JSON.

- [ ] **Step 3: Adjust `monitor/app/api/aliases.py` to coexist with the web GET**

The web router will serve `/aliases` as HTML; rename the JSON list endpoint to `/api/aliases` for the API and keep `/aliases` POST as the action target. Replace the file:

```python
"""Public, editable alias map. No auth.

GET  /api/aliases       → JSON list (consumed by older clients/tests)
GET  /api/aliases/members → JSON list of distinct alias values
POST /aliases            → form-or-JSON upsert; returns row partial on HTMX, 303 redirect on plain
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.api.sse import broadcaster
from app.core.aliases import known_members, list_aliases, upsert_alias
from app.deps import get_db


_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


router = APIRouter()


class UpsertAliasRequest(BaseModel):
    device_name: str = Field(min_length=1, max_length=255)
    alias: str = Field(min_length=1, max_length=64)


async def _do_upsert(conn: sqlite3.Connection, device_name: str, alias: str) -> str:
    try:
        upsert_alias(conn, device_name=device_name, alias=alias)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    cleaned = alias.strip()
    await broadcaster.publish(
        {"type": "alias.updated", "device_name": device_name, "alias": cleaned}
    )
    return cleaned


@router.post("/aliases")
async def upsert(
    request: Request,
    hx_request: str | None = Header(default=None, alias="HX-Request"),
    conn: sqlite3.Connection = Depends(get_db),
):
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        body = UpsertAliasRequest.model_validate(await request.json())
        cleaned = await _do_upsert(conn, body.device_name, body.alias)
        return {"device_name": body.device_name, "alias": cleaned}

    form = await request.form()
    device_name = (form.get("device_name") or "").strip()
    alias = (form.get("alias") or "").strip()
    if not device_name or not alias:
        raise HTTPException(status_code=422, detail="device_name and alias required")
    cleaned = await _do_upsert(conn, device_name, alias)

    if hx_request == "true":
        return _TEMPLATES.TemplateResponse(
            request,
            "_partials/alias_row.html",
            {
                "row": {"device_name": device_name, "alias": cleaned, "updated_at": ""},
            },
        )
    return RedirectResponse(url="/aliases", status_code=303)


@router.get("/api/aliases")
def list_all(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return {"items": [dict(r) for r in list_aliases(conn)]}


@router.get("/api/aliases/members")
def members(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return {"members": known_members(conn)}
```

> **Note:** the existing `test_api_aliases.py` calls `/aliases` GET, which now belongs to the web router. Update that test in step 6.

- [ ] **Step 4: Add the web GET in `monitor/app/api/web.py`**

Append:
```python
from app.core.aliases import list_aliases as _list_aliases


@router.get("/aliases", response_class=HTMLResponse)
def aliases_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return _TEMPLATES.TemplateResponse(
        request,
        "aliases.html",
        {
            "display_tz": settings.display_tz,
            "rows": [dict(r) for r in _list_aliases(conn)],
        },
    )
```

- [ ] **Step 5: Write `monitor/app/templates/aliases.html`**

```html
{% extends "base.html" %}
{% block title %}Aliases — Server Monitor{% endblock %}
{% block content %}
<h2>Device aliases</h2>
<p>Map raw device names to people. Anyone on the LAN can add or change these.</p>

<table>
    <thead><tr><th>Device name</th><th>Alias</th><th>Updated</th><th></th></tr></thead>
    <tbody id="alias-tbody">
        {% for row in rows %}
            {% include "_partials/alias_row.html" %}
        {% endfor %}
    </tbody>
</table>

<h3>Add or change an alias</h3>
<form method="post" action="/aliases" hx-post="/aliases" hx-target="#alias-tbody" hx-swap="afterbegin">
    <input name="device_name" placeholder="DESKTOP-AB12C" required>
    <input name="alias" placeholder="alice" required>
    <button type="submit">Save</button>
</form>
{% endblock %}
```

- [ ] **Step 6: Write `monitor/app/templates/_partials/alias_row.html`**

```html
<tr>
    <td><code>{{ row.device_name }}</code></td>
    <td>
        <form method="post" action="/aliases"
              hx-post="/aliases" hx-swap="outerHTML" hx-target="closest tr">
            <input type="hidden" name="device_name" value="{{ row.device_name }}">
            <input name="alias" value="{{ row.alias }}" required>
            <button type="submit" class="secondary">Save</button>
        </form>
    </td>
    <td><small data-iso="{{ row.updated_at }}"></small></td>
    <td></td>
</tr>
```

- [ ] **Step 7: Update existing alias tests for renamed JSON endpoints**

Edit `monitor/tests/test_api_aliases.py`:
```python
async def test_upsert_alias_then_list(client) -> None:
    r = await client.post(
        "/aliases", json={"device_name": "DESKTOP-AB12C", "alias": "alice"}
    )
    assert r.status_code == 200
    rl = await client.get("/api/aliases")
    items = rl.json()["items"]
    assert any(i["device_name"] == "DESKTOP-AB12C" and i["alias"] == "alice" for i in items)


async def test_known_members_list(client) -> None:
    await client.post("/aliases", json={"device_name": "A", "alias": "alice"})
    await client.post("/aliases", json={"device_name": "B", "alias": "bob"})
    r = await client.get("/api/aliases/members")
    assert r.json()["members"] == ["alice", "bob"]


async def test_empty_alias_returns_422(client) -> None:
    r = await client.post("/aliases", json={"device_name": "A", "alias": "   "})
    assert r.status_code == 422


async def test_alias_change_publishes_sse(client) -> None:
    from app.api.sse import broadcaster

    q = broadcaster.subscribe()
    try:
        await client.post("/aliases", json={"device_name": "A", "alias": "alice"})
        evt = q.get_nowait()
        assert evt["type"] == "alias.updated"
        assert evt["device_name"] == "A"
        assert evt["alias"] == "alice"
    finally:
        broadcaster.unsubscribe(q)
```

- [ ] **Step 8: Run all monitor tests**

Run: `.venv/bin/pytest monitor/tests -q`
Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add monitor/app/api/aliases.py monitor/app/api/web.py \
        monitor/app/templates/aliases.html monitor/app/templates/_partials/alias_row.html \
        monitor/tests/test_api_aliases.py monitor/tests/test_web_aliases.py
git commit -m "feat(monitor): aliases page with HTMX inline edit"
```

### Task 3.4: Server detail page with day grid + booking

**Files:**
- Modify: `monitor/app/api/web.py`
- Create: `monitor/app/templates/server_detail.html`
- Create: `monitor/app/templates/_partials/booking_cell.html`
- Create: `monitor/app/templates/_partials/booking_modal.html`
- Modify: `monitor/app/api/bookings.py` (HTMX-aware response)
- Create: `monitor/tests/test_web_server_detail.py`

- [ ] **Step 1: Write `monitor/tests/test_web_server_detail.py`**

```python
async def test_server_detail_renders_day_grid(client) -> None:
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    sid = r.json()["server_id"]
    body = (await client.get(f"/server/{sid}?day=2030-01-01")).text
    # 48 half-hour cells
    assert body.count("sm-slot") >= 48
    assert "00:00" in body and "23:30" in body


async def test_server_detail_marks_booked_cell(client) -> None:
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    sid = r.json()["server_id"]
    await client.post(
        "/bookings",
        json={"server_id": sid, "start_at": "2030-01-01T14:00:00+00:00", "member_name": "alice"},
    )
    body = (await client.get(f"/server/{sid}?day=2030-01-01")).text
    assert "sm-slot--booked" in body
    assert "alice" in body


async def test_server_detail_unknown_id_returns_404(client) -> None:
    r = await client.get("/server/999?day=2030-01-01")
    assert r.status_code == 404
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_web_server_detail.py -v`
Expected: FAIL.

- [ ] **Step 3: Append the web route in `monitor/app/api/web.py`**

```python
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException

from app.core.bookings import list_bookings_for_day
from app.core.aliases import known_members as _known_members


def _server_or_404(conn: sqlite3.Connection, server_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM servers WHERE id=?", (server_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return row


def _day_or_today(day_str: str | None) -> date:
    if not day_str:
        return datetime.now(UTC).date()
    try:
        return date.fromisoformat(day_str)
    except ValueError as e:
        raise HTTPException(status_code=422, detail="invalid day") from e


@router.get("/server/{server_id}", response_class=HTMLResponse)
def server_detail(
    server_id: int,
    request: Request,
    day: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    row = _server_or_404(conn, server_id)
    d = _day_or_today(day)
    bookings = list_bookings_for_day(conn, server_id=server_id, day=d)
    by_start = {b["start_at"]: dict(b) for b in bookings}

    base = datetime(d.year, d.month, d.day, tzinfo=UTC)
    slots = []
    for i in range(48):
        start = base + timedelta(minutes=30 * i)
        slots.append({
            "start_at": start.isoformat(),
            "label": start.strftime("%H:%M"),
            "booking": by_start.get(start.isoformat()),
            "is_past": start < datetime.now(UTC),
        })

    days = [(datetime.now(UTC).date() + timedelta(days=i)) for i in range(7)]

    return _TEMPLATES.TemplateResponse(
        request,
        "server_detail.html",
        {
            "display_tz": settings.display_tz,
            "server": dict(row),
            "day": d.isoformat(),
            "slots": slots,
            "days": [x.isoformat() for x in days],
            "members": _known_members(conn),
        },
    )
```

- [ ] **Step 4: Write `monitor/app/templates/server_detail.html`**

```html
{% extends "base.html" %}
{% block title %}{{ server.hostname }} — Server Monitor{% endblock %}
{% block content %}
<nav>
    <a href="/" class="secondary">&larr; Dashboard</a>
</nav>
<h2>{{ server.hostname }} <small>({{ server.os }})</small></h2>

<form method="get" action="/server/{{ server.id }}">
    <label for="day">Day:</label>
    <select id="day" name="day" onchange="this.form.submit()">
        {% for d in days %}
            <option value="{{ d }}" {% if d == day %}selected{% endif %}>{{ d }}</option>
        {% endfor %}
    </select>
</form>

<div class="sm-day-grid" id="day-grid">
    {% for slot in slots %}
        {% include "_partials/booking_cell.html" %}
    {% endfor %}
</div>

{% include "_partials/booking_modal.html" %}
{% endblock %}
```

- [ ] **Step 5: Write `monitor/app/templates/_partials/booking_cell.html`**

```html
<div>{{ slot.label }}</div>
<button type="button"
        class="sm-slot
            {% if slot.booking %}sm-slot--booked{% endif %}
            {% if slot.is_past %}sm-slot--past{% endif %}"
        {% if slot.booking %}
            data-booking-id="{{ slot.booking.id }}"
            data-booking-member="{{ slot.booking.member_name }}"
            disabled
        {% else %}
            x-on:click="$dispatch('sm-open-booking', { startAt: '{{ slot.start_at }}', label: '{{ slot.label }}' })"
            {% if slot.is_past %}disabled{% endif %}
        {% endif %}>
    {% if slot.booking %}{{ slot.booking.member_name }}{% else %}+{% endif %}
</button>
```

- [ ] **Step 6: Write `monitor/app/templates/_partials/booking_modal.html`**

```html
<div x-data="{ open: false, startAt: '', label: '' }"
     x-on:sm-open-booking.window="open = true; startAt = $event.detail.startAt; label = $event.detail.label">

    <div class="sm-modal" x-bind:hidden="!open">
        <div class="sm-modal__panel" x-show="open">
            <h4>Book <span x-text="label"></span></h4>
            <form hx-post="/bookings" hx-ext="json-enc" hx-swap="none"
                  x-on:htmx:after-request.window="if ($event.detail.successful) location.reload()">
                <input type="hidden" name="server_id" value="{{ server.id }}">
                <input type="hidden" name="start_at" x-bind:value="startAt">
                <label>
                    Your name
                    <input name="member_name" list="known-members" required autofocus>
                </label>
                <datalist id="known-members">
                    {% for m in members %}<option value="{{ m }}"></option>{% endfor %}
                </datalist>
                <label>
                    Note (optional)
                    <input name="note" maxlength="200">
                </label>
                <div>
                    <button type="submit">Confirm</button>
                    <button type="button" class="secondary" x-on:click="open = false">Cancel</button>
                </div>
            </form>
        </div>
    </div>
</div>

<!-- json-enc HTMX extension serializes form fields as JSON for our /bookings endpoint -->
<script src="https://unpkg.com/htmx.org@1.9.12/dist/ext/json-enc.js"></script>
```

- [ ] **Step 7: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_web_server_detail.py -v`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add monitor/app/api/web.py monitor/app/templates/server_detail.html \
        monitor/app/templates/_partials/booking_cell.html \
        monitor/app/templates/_partials/booking_modal.html \
        monitor/tests/test_web_server_detail.py
git commit -m "feat(monitor): server detail page with day grid + booking modal"
```

### Task 3.5: Enroll page (admin)

**Files:**
- Modify: `monitor/app/api/web.py`
- Create: `monitor/app/templates/enroll.html`
- Create: `monitor/tests/test_web_enroll.py`

- [ ] **Step 1: Write `monitor/tests/test_web_enroll.py`**

```python
async def test_enroll_page_lists_servers(client) -> None:
    await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    body = (await client.get("/enroll")).text
    assert "srv-a" in body
    assert "<form" in body  # creation form present


async def test_enroll_page_form_creates_pending_server_and_shows_command(client) -> None:
    r = await client.post(
        "/enroll",
        data={"hostname": "srv-b", "os": "linux"},
    )
    assert r.status_code == 200
    body = r.text
    assert "srv-b" in body
    assert "install.sh" in body  # the install command is displayed
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_web_enroll.py -v`
Expected: FAIL.

- [ ] **Step 3: Add web routes to `monitor/app/api/web.py`**

Append:
```python
from fastapi import Form

from app.core.servers import create_pending_server, list_servers as _list_servers


@router.get("/enroll", response_class=HTMLResponse)
def enroll_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return _TEMPLATES.TemplateResponse(
        request,
        "enroll.html",
        {
            "display_tz": settings.display_tz,
            "monitor_host": settings.monitor_host,
            "rows": [dict(r) for r in _list_servers(conn)],
            "command": None,
            "selected_os": None,
        },
    )


@router.post("/enroll", response_class=HTMLResponse)
def enroll_create(
    request: Request,
    hostname: str = Form(...),
    os: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    sid, token = create_pending_server(
        conn, hostname=hostname, os=os, ttl_seconds=settings.enrollment_token_ttl
    )
    return _TEMPLATES.TemplateResponse(
        request,
        "enroll.html",
        {
            "display_tz": settings.display_tz,
            "monitor_host": settings.monitor_host,
            "rows": [dict(r) for r in _list_servers(conn)],
            "command": {"hostname": hostname, "os": os, "token": token},
            "selected_os": os,
        },
    )
```

- [ ] **Step 4: Write `monitor/app/templates/enroll.html`**

```html
{% extends "base.html" %}
{% block title %}Enroll a server — Server Monitor{% endblock %}
{% block content %}
<h2>Enroll a server</h2>
<form method="post" action="/enroll">
    <label>Hostname <input name="hostname" required></label>
    <label>OS
        <select name="os">
            <option value="linux">Linux</option>
            <option value="windows">Windows</option>
        </select>
    </label>
    <button type="submit">Generate install command</button>
</form>

{% if command %}
    <article>
        <h3>Run on <code>{{ command.hostname }}</code></h3>
        {% if command.os == "linux" %}
            <pre><code>curl -fsSL https://{{ monitor_host }}/install.sh | sudo bash -s -- \
  --token {{ command.token }} \
  --hostname {{ command.hostname }}</code></pre>
        {% else %}
            <pre><code>iwr https://{{ monitor_host }}/install.ps1 -UseBasicParsing | iex; `
Install-MonitorAgent -Token {{ command.token }} -Hostname {{ command.hostname }}</code></pre>
        {% endif %}
        <p><small>Token is one-time; expires in {{ 3600 }} seconds.</small></p>
    </article>
{% endif %}

<h3>Servers</h3>
<table>
    <thead><tr><th>Hostname</th><th>OS</th><th>Status</th><th>Last seen</th></tr></thead>
    <tbody>
        {% for r in rows %}
        <tr>
            <td>{{ r.hostname }}</td>
            <td>{{ r.os }}</td>
            <td>{% if r.enrolled %}enrolled{% else %}pending{% endif %}</td>
            <td><small data-iso="{{ r.last_seen_at }}"></small></td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% endblock %}
```

- [ ] **Step 5: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_web_enroll.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add monitor/app/api/web.py monitor/app/templates/enroll.html \
        monitor/tests/test_web_enroll.py
git commit -m "feat(monitor): enroll page with copy-paste install command"
```

### Task 3.6: SSE client wiring (live dashboard updates)

**Files:**
- Modify: `monitor/app/static/app.js`
- Modify: `monitor/app/templates/_partials/server_card.html`
- Create: `monitor/tests/test_web_sse_smoke.py`

- [ ] **Step 1: Write `monitor/tests/test_web_sse_smoke.py`**

```python
async def test_dashboard_html_subscribes_to_sse(client) -> None:
    body = (await client.get("/")).text
    assert "EventSource" in body or "sm:event" in body
    assert "id=\"server-grid\"" in body


async def test_event_includes_server_id_for_targeted_swap(client) -> None:
    """When the server publishes a 'report' event the SSE payload exposes hostname."""
    from app.api.sse import broadcaster

    q = broadcaster.subscribe()
    try:
        # Set up an enrolled server and post a report so the event fires.
        r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
        token = (await client.post(
            "/api/enroll",
            json={"hostname": "srv-a", "enrollment_token": r.json()["enrollment_token"]},
        )).json()["agent_token"]

        await client.post(
            "/api/report",
            json={
                "hostname": "srv-a",
                "received_at": "2030-01-01T12:00:00+00:00",
                "sessions": [],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        evt = q.get_nowait()
        assert evt["hostname"] == "srv-a"
    finally:
        broadcaster.unsubscribe(q)
```

- [ ] **Step 2: Run; first assertion already passes from base.html, second already passes from Task 2.5. Verify.**

Run: `.venv/bin/pytest monitor/tests/test_web_sse_smoke.py -v`
Expected: 2 passed.

- [ ] **Step 3: Make the dashboard reload its grid in response to events**

Replace `monitor/app/static/app.js`:
```javascript
// Time formatting
function relativeLocal(iso) {
    if (!iso) return "";
    const dt = new Date(iso);
    const diff = Math.round((Date.now() - dt.getTime()) / 1000);
    if (diff < 60) return diff + "s ago";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    return Math.floor(diff / 3600) + "h ago";
}
function applyRelative(scope) {
    (scope || document).querySelectorAll("[data-iso]").forEach(el => {
        el.textContent = relativeLocal(el.getAttribute("data-iso"));
    });
}

// Refresh strategy: when an SSE event arrives that affects what we're showing,
// HTMX swaps the relevant fragment. For now we do a coarse-grained refresh of
// the whole dashboard grid since at <=20 servers it's negligible.
function refreshGrid() {
    const grid = document.getElementById("server-grid");
    if (!grid) return;
    fetch("/?fragment=grid", { headers: { "HX-Request": "true" } })
        .then(r => r.text())
        .then(html => {
            grid.outerHTML = html;
            applyRelative();
        })
        .catch(() => { /* silent */ });
}

// React to SSE events
window.addEventListener("sm:event", function (e) {
    const evt = e.detail || {};
    if (evt.type === "report" || evt.type === "alias.updated"
        || evt.type === "server.online" || evt.type === "server.offline") {
        if (document.getElementById("server-grid")) refreshGrid();
    }
});

document.addEventListener("DOMContentLoaded", () => applyRelative());
document.addEventListener("htmx:afterSwap", e => applyRelative(e.target));
setInterval(() => applyRelative(), 30 * 1000);
```

- [ ] **Step 4: Add a fragment-only response in `monitor/app/api/web.py`**

Modify `dashboard()`:
```python
@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    fragment: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    servers = [_server_view(conn, r) for r in list_servers(conn)]
    if fragment == "grid":
        return _TEMPLATES.TemplateResponse(
            request,
            "_partials/server_grid.html",
            {"servers": servers},
        )
    return _TEMPLATES.TemplateResponse(
        request,
        "dashboard.html",
        {"display_tz": settings.display_tz, "servers": servers},
    )
```

Create `monitor/app/templates/_partials/server_grid.html`:
```html
<div class="sm-grid" id="server-grid">
    {% for s in servers %}
        {% include "_partials/server_card.html" %}
    {% else %}
        <p>No servers yet. <a href="/enroll">Add one</a>.</p>
    {% endfor %}
</div>
```

Replace the grid block in `monitor/app/templates/dashboard.html`:
```html
{% extends "base.html" %}
{% block title %}Dashboard — Server Monitor{% endblock %}
{% block content %}
<h2>Servers</h2>
{% include "_partials/server_grid.html" %}
{% endblock %}
```

- [ ] **Step 5: Run all monitor tests**

Run: `.venv/bin/pytest monitor/tests -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add monitor/app/static/app.js monitor/app/api/web.py \
        monitor/app/templates/dashboard.html \
        monitor/app/templates/_partials/server_grid.html \
        monitor/tests/test_web_sse_smoke.py
git commit -m "feat(monitor): live dashboard updates via SSE-driven fragment refresh"
```

---
## Phase 4 — Agent core

The agent is one Python package, packaged per-OS via PyInstaller. Pure logic and the HTTP client are testable cross-platform; the Windows collector is unit-tested with mocks (real Windows verification happens in Phase 8).

### Task 4.1: Snapshot type + diff

**Files:**
- Create: `agent/server_monitor_agent/snapshot.py`
- Create: `agent/tests/test_snapshot.py`

- [ ] **Step 1: Write `agent/tests/test_snapshot.py`**

```python
"""Snapshot diffing — what changed between two consecutive collects."""

from __future__ import annotations


def _s(device: str, state: str = "active") -> dict:
    return {
        "device_name": device,
        "username": "shared",
        "protocol": "rdp",
        "state": state,
        "logon_at": "2030-01-01T11:00:00+00:00",
    }


def test_diff_detects_added_removed_changed() -> None:
    from server_monitor_agent.snapshot import diff_snapshots

    a = [_s("A"), _s("B")]
    b = [_s("A", state="disconnected"), _s("C")]
    d = diff_snapshots(a, b)
    assert [x["device_name"] for x in d.added] == ["C"]
    assert [x["device_name"] for x in d.removed] == ["B"]
    assert [x["device_name"] for x in d.changed] == ["A"]


def test_diff_empty_is_noop() -> None:
    from server_monitor_agent.snapshot import diff_snapshots

    d = diff_snapshots([_s("A")], [_s("A")])
    assert d.added == [] and d.removed == [] and d.changed == []


def test_diff_is_pure() -> None:
    from server_monitor_agent.snapshot import diff_snapshots

    a = [_s("A")]
    b = [_s("B")]
    diff_snapshots(a, b)
    assert a == [_s("A")] and b == [_s("B")]
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest agent/tests/test_snapshot.py -v`
Expected: FAIL.

- [ ] **Step 3: Write `agent/server_monitor_agent/snapshot.py`**

```python
"""Pure snapshot helpers used by the agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict


class Session(TypedDict):
    device_name: str
    username: str | None
    protocol: str  # 'rdp' | 'ssh' | 'console'
    state: str  # 'active' | 'disconnected'
    logon_at: str  # ISO-8601 UTC


@dataclass
class Diff:
    added: list[Session] = field(default_factory=list)
    removed: list[Session] = field(default_factory=list)
    changed: list[Session] = field(default_factory=list)


def _by_device(items: list[Session]) -> dict[str, Session]:
    return {s["device_name"]: s for s in items}


def diff_snapshots(prev: list[Session], curr: list[Session]) -> Diff:
    p, c = _by_device(prev), _by_device(curr)
    out = Diff()
    for k, v in c.items():
        if k not in p:
            out.added.append(v)
        elif v != p[k]:
            out.changed.append(v)
    for k, v in p.items():
        if k not in c:
            out.removed.append(v)
    return out


def is_changed(prev: list[Session], curr: list[Session]) -> bool:
    d = diff_snapshots(prev, curr)
    return bool(d.added or d.removed or d.changed)
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest agent/tests/test_snapshot.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/server_monitor_agent/snapshot.py agent/tests/test_snapshot.py
git commit -m "feat(agent): snapshot type + diff helper"
```

### Task 4.2: Linux collector (parses `who` / `loginctl`)

**Files:**
- Create: `agent/server_monitor_agent/collect_linux.py`
- Create: `agent/tests/test_collect_linux.py`

- [ ] **Step 1: Write `agent/tests/test_collect_linux.py`**

```python
"""Parse fixtures emulating `who -u` and `loginctl list-sessions` output."""

from __future__ import annotations


WHO_FIXTURE = (
    "alice    pts/0        2030-01-01 11:55 (192.168.1.42)\n"
    "alice    tty1         2030-01-01 09:00\n"
)


def test_parse_who_extracts_remote_ssh_session() -> None:
    from server_monitor_agent.collect_linux import parse_who

    sessions = parse_who(WHO_FIXTURE)
    devices = {s["device_name"] for s in sessions}
    assert "192.168.1.42" in devices

    s = next(s for s in sessions if s["device_name"] == "192.168.1.42")
    assert s["protocol"] == "ssh"
    assert s["state"] == "active"
    assert s["username"] == "alice"
    assert s["logon_at"].startswith("2030-01-01T11:55")


def test_parse_who_marks_console_session() -> None:
    from server_monitor_agent.collect_linux import parse_who

    sessions = parse_who(WHO_FIXTURE)
    s = next(s for s in sessions if s["protocol"] == "console")
    assert s["device_name"] == "tty1"
    assert s["state"] == "active"


def test_parse_who_handles_empty_input() -> None:
    from server_monitor_agent.collect_linux import parse_who

    assert parse_who("") == []
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest agent/tests/test_collect_linux.py -v`
Expected: FAIL.

- [ ] **Step 3: Write `agent/server_monitor_agent/collect_linux.py`**

```python
"""Read login sessions on Linux via `who -u`.

`who -u` lines:
    alice    pts/0        2030-01-01 11:55 (192.168.1.42)   <- remote ssh
    alice    tty1         2030-01-01 09:00                  <- local console

We could also use `loginctl list-sessions --json`, but `who` is universally
available and stable. Keep it simple.
"""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime

from server_monitor_agent.snapshot import Session


# username, line, date, time, optional (remote)
_LINE = re.compile(
    r"^(?P<user>\S+)\s+(?P<line>\S+)\s+(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2})"
    r"(?:\s+\((?P<remote>[^)]+)\))?"
)


def _to_iso(date_part: str, time_part: str) -> str:
    dt = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
    return dt.isoformat()


def parse_who(output: str) -> list[Session]:
    out: list[Session] = []
    for raw in output.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        m = _LINE.match(line)
        if not m:
            continue
        username = m.group("user")
        terminal = m.group("line")
        remote = m.group("remote")
        logon = _to_iso(m.group("date"), m.group("time"))
        if remote:
            out.append({
                "device_name": remote,
                "username": username,
                "protocol": "ssh",
                "state": "active",
                "logon_at": logon,
            })
        else:
            out.append({
                "device_name": terminal,
                "username": username,
                "protocol": "console",
                "state": "active",
                "logon_at": logon,
            })
    return out


def collect() -> list[Session]:
    """Run `who -u` and parse the output. Returns empty on failure."""
    try:
        result = subprocess.run(
            ["who", "-u"], capture_output=True, text=True, timeout=5, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return parse_who(result.stdout)
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest agent/tests/test_collect_linux.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/server_monitor_agent/collect_linux.py agent/tests/test_collect_linux.py
git commit -m "feat(agent): Linux session collector"
```

### Task 4.3: Windows collector (mocked tests; verified manually in Phase 8)

**Files:**
- Create: `agent/server_monitor_agent/collect_windows.py`
- Create: `agent/tests/test_collect_windows.py`

- [ ] **Step 1: Write `agent/tests/test_collect_windows.py`**

```python
"""The Windows collector wraps WTS APIs; tests mock the pywin32 surface.

Verified end-to-end manually on a real Windows host (see Phase 8).
"""

from __future__ import annotations

import sys
import types

import pytest


class _FakeWin32ts:
    WTS_CURRENT_SERVER_HANDLE = 0
    WTSActive = 0
    WTSDisconnected = 4
    WTSConnectState = 8
    WTSClientName = 10
    WTSUserName = 5

    def __init__(self, sessions: list[dict]) -> None:
        self._sessions = sessions

    def WTSEnumerateSessions(self, _h):  # noqa: N802
        return [(s["id"], s.get("name", "rdp-tcp"), s.get("state", self.WTSActive)) for s in self._sessions]

    def WTSQuerySessionInformation(self, _h, sid, code):  # noqa: N802
        for s in self._sessions:
            if s["id"] == sid:
                if code == self.WTSClientName:
                    return s.get("client_name", "")
                if code == self.WTSUserName:
                    return s.get("user", "")
                if code == self.WTSConnectState:
                    return s.get("state", self.WTSActive)
        return ""


@pytest.fixture
def fake_wts(monkeypatch: pytest.MonkeyPatch):
    def install(sessions: list[dict]) -> _FakeWin32ts:
        fake = _FakeWin32ts(sessions)
        mod = types.ModuleType("win32ts")
        for attr in (
            "WTS_CURRENT_SERVER_HANDLE", "WTSActive", "WTSDisconnected",
            "WTSConnectState", "WTSClientName", "WTSUserName",
        ):
            setattr(mod, attr, getattr(fake, attr))
        mod.WTSEnumerateSessions = fake.WTSEnumerateSessions
        mod.WTSQuerySessionInformation = fake.WTSQuerySessionInformation
        monkeypatch.setitem(sys.modules, "win32ts", mod)
        return fake
    return install


def test_collect_returns_active_rdp_session_with_client_name(fake_wts) -> None:
    fake_wts([{"id": 1, "state": 0, "client_name": "LAPTOP-A", "user": "shared"}])

    from server_monitor_agent.collect_windows import collect

    out = collect()
    assert len(out) == 1
    s = out[0]
    assert s["device_name"] == "LAPTOP-A"
    assert s["state"] == "active"
    assert s["protocol"] == "rdp"
    assert s["username"] == "shared"


def test_collect_skips_session_without_client_name(fake_wts) -> None:
    """Session 0 (the services session) has no client name; ignore it."""
    fake_wts([{"id": 0, "state": 0, "client_name": "", "user": "SYSTEM"}])

    from server_monitor_agent.collect_windows import collect

    assert collect() == []


def test_collect_marks_disconnected_state(fake_wts) -> None:
    fake_wts([{"id": 2, "state": 4, "client_name": "LAPTOP-B", "user": "shared"}])

    from server_monitor_agent.collect_windows import collect

    out = collect()
    assert out and out[0]["state"] == "disconnected"
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest agent/tests/test_collect_windows.py -v`
Expected: FAIL.

- [ ] **Step 3: Write `agent/server_monitor_agent/collect_windows.py`**

```python
"""Read RDP sessions on Windows via the WTS API.

We use pywin32's win32ts module. Logon time isn't directly returned by
WTSQuerySessionInformation in older Windows builds, so we approximate by
remembering the first time we saw a (session_id, client_name) tuple.
This is good enough for "minutes ago" display.

Verified manually on Windows Server 2019/2022 — see Phase 8.
"""

from __future__ import annotations

from datetime import UTC, datetime

from server_monitor_agent.snapshot import Session


_FIRST_SEEN: dict[tuple[int, str], str] = {}


def _state_to_string(value: int) -> str:
    # WTSActive == 0; WTSDisconnected == 4; everything else is treated as active for our purposes.
    return "disconnected" if value == 4 else "active"


def collect() -> list[Session]:
    try:
        import win32ts  # type: ignore[import-not-found]
    except ImportError:
        return []

    out: list[Session] = []
    handle = win32ts.WTS_CURRENT_SERVER_HANDLE
    for session_id, _name, state in win32ts.WTSEnumerateSessions(handle):
        try:
            client_name = win32ts.WTSQuerySessionInformation(handle, session_id, win32ts.WTSClientName) or ""
            user = win32ts.WTSQuerySessionInformation(handle, session_id, win32ts.WTSUserName) or ""
        except Exception:  # noqa: BLE001
            continue
        if not client_name:
            continue  # services session, console without remote client, etc.
        key = (session_id, client_name)
        first = _FIRST_SEEN.setdefault(key, datetime.now(UTC).isoformat())
        out.append({
            "device_name": client_name,
            "username": user or None,
            "protocol": "rdp",
            "state": _state_to_string(state),
            "logon_at": first,
        })
    # Drop stale first-seen entries no longer present in this enumeration.
    seen = {(sid, n) for sid, n in ((sess["device_name"],) for sess in out)}  # noqa: E501
    for key in list(_FIRST_SEEN.keys()):
        if key[1] not in {s["device_name"] for s in out}:
            _FIRST_SEEN.pop(key, None)
    return out
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest agent/tests/test_collect_windows.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/server_monitor_agent/collect_windows.py agent/tests/test_collect_windows.py
git commit -m "feat(agent): Windows RDP session collector (pywin32, mocked tests)"
```

### Task 4.4: Cross-platform collector dispatch

**Files:**
- Create: `agent/server_monitor_agent/collect.py`
- Create: `agent/tests/test_collect_dispatch.py`

- [ ] **Step 1: Write `agent/tests/test_collect_dispatch.py`**

```python
def test_collect_uses_linux_on_linux(monkeypatch) -> None:
    import sys

    monkeypatch.setattr(sys, "platform", "linux")

    captured = {}

    def fake_linux():
        captured["called"] = "linux"
        return []

    from server_monitor_agent import collect

    monkeypatch.setattr(collect, "_collect_linux", fake_linux)
    collect.collect()
    assert captured["called"] == "linux"


def test_collect_uses_windows_on_win32(monkeypatch) -> None:
    import sys

    monkeypatch.setattr(sys, "platform", "win32")

    captured = {}

    def fake_windows():
        captured["called"] = "windows"
        return []

    from server_monitor_agent import collect

    monkeypatch.setattr(collect, "_collect_windows", fake_windows)
    collect.collect()
    assert captured["called"] == "windows"
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest agent/tests/test_collect_dispatch.py -v`
Expected: FAIL.

- [ ] **Step 3: Write `agent/server_monitor_agent/collect.py`**

```python
"""Pick the right per-OS collector at call time."""

from __future__ import annotations

import sys

from server_monitor_agent.snapshot import Session


def _collect_linux() -> list[Session]:
    from server_monitor_agent.collect_linux import collect as _c

    return _c()


def _collect_windows() -> list[Session]:
    from server_monitor_agent.collect_windows import collect as _c

    return _c()


def collect() -> list[Session]:
    if sys.platform == "win32":
        return _collect_windows()
    return _collect_linux()
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest agent/tests/test_collect_dispatch.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/server_monitor_agent/collect.py agent/tests/test_collect_dispatch.py
git commit -m "feat(agent): cross-platform collector dispatch"
```

### Task 4.5: Per-OS token storage

**Files:**
- Create: `agent/server_monitor_agent/token_store.py`
- Create: `agent/tests/test_token_store.py`

- [ ] **Step 1: Write `agent/tests/test_token_store.py`**

```python
import os
import stat
import sys
from pathlib import Path


def test_save_and_load_round_trip(token_file: Path) -> None:
    from server_monitor_agent.token_store import load_token, save_token

    save_token(token_file, "abc-123")
    assert load_token(token_file) == "abc-123"


def test_save_uses_restrictive_mode_on_posix(token_file: Path) -> None:
    if sys.platform == "win32":
        return
    from server_monitor_agent.token_store import save_token

    save_token(token_file, "x")
    mode = os.stat(token_file).st_mode & 0o777
    assert mode == 0o600


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    from server_monitor_agent.token_store import load_token

    assert load_token(tmp_path / "no-such") is None


def test_default_path_per_os() -> None:
    from server_monitor_agent.token_store import default_token_path

    p = default_token_path()
    if sys.platform == "win32":
        assert "server-monitor-agent" in str(p)
    else:
        assert str(p) == "/etc/server-monitor-agent/token"
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest agent/tests/test_token_store.py -v`
Expected: FAIL.

- [ ] **Step 3: Write `agent/server_monitor_agent/token_store.py`**

```python
"""Read/write the agent's long-lived token in an OS-protected file."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def default_token_path() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("ProgramData", r"C:\\ProgramData")
        return Path(base) / "server-monitor-agent" / "token"
    return Path("/etc/server-monitor-agent/token")


def save_token(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write with restrictive mode on POSIX. On Windows we rely on directory ACLs
    # set by the install script (SYSTEM + Administrators only).
    if sys.platform == "win32":
        path.write_text(token, encoding="ascii")
        return
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("ascii"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def load_token(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip() or None
    except FileNotFoundError:
        return None
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest agent/tests/test_token_store.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/server_monitor_agent/token_store.py agent/tests/test_token_store.py
git commit -m "feat(agent): per-OS token storage with POSIX 0600 mode"
```

### Task 4.6: HTTP client (enroll + report)

**Files:**
- Create: `agent/server_monitor_agent/client.py`
- Create: `agent/tests/test_client.py`

- [ ] **Step 1: Write `agent/tests/test_client.py`**

```python
"""HTTP client uses bearer auth and the right URL paths."""

from __future__ import annotations

import json

import httpx
import pytest


def _mock_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_enroll_returns_agent_token() -> None:
    from server_monitor_agent.client import Client

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/enroll"
        body = json.loads(request.content)
        assert body == {"hostname": "h", "enrollment_token": "tok"}
        return httpx.Response(200, json={"agent_token": "agent-tok"})

    c = Client(base_url="https://m.lan", transport=_mock_transport(handler), verify=False)
    assert await c.enroll(hostname="h", enrollment_token="tok") == "agent-tok"


@pytest.mark.asyncio
async def test_report_uses_bearer_header() -> None:
    from server_monitor_agent.client import Client

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer agent-tok"
        body = json.loads(request.content)
        assert body["hostname"] == "h"
        assert body["sessions"] == [{"x": 1}]
        return httpx.Response(200, json={"accepted": True})

    c = Client(base_url="https://m.lan", transport=_mock_transport(handler), verify=False)
    await c.report(
        hostname="h",
        token="agent-tok",
        sessions=[{"x": 1}],
        received_at="2030-01-01T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_report_raises_on_401() -> None:
    from server_monitor_agent.client import AuthError, Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "nope"})

    c = Client(base_url="https://m.lan", transport=_mock_transport(handler), verify=False)
    with pytest.raises(AuthError):
        await c.report(
            hostname="h", token="bad", sessions=[],
            received_at="2030-01-01T00:00:00+00:00",
        )
```

Add `pytest-asyncio` requirement to `agent/pyproject.toml` `dev` extras (top of file). Update file:

```toml
[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-asyncio>=0.23",
  "pyinstaller>=6.0",
  "ruff>=0.4",
]
```

Then `.venv/bin/pip install -e 'agent[dev]'` to refresh deps.

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest agent/tests/test_client.py -v`
Expected: FAIL.

- [ ] **Step 3: Write `agent/server_monitor_agent/client.py`**

```python
"""Tiny async HTTP client for the agent."""

from __future__ import annotations

import httpx


class ClientError(RuntimeError):
    pass


class AuthError(ClientError):
    pass


class Client:
    def __init__(
        self,
        *,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        verify: bool | str = True,
        timeout: float = 10.0,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            verify=verify,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def enroll(self, *, hostname: str, enrollment_token: str) -> str:
        r = await self._client.post(
            "/api/enroll",
            json={"hostname": hostname, "enrollment_token": enrollment_token},
        )
        if r.status_code == 401:
            raise AuthError(r.json().get("detail", "unauthorized"))
        r.raise_for_status()
        return r.json()["agent_token"]

    async def report(
        self,
        *,
        hostname: str,
        token: str,
        sessions: list[dict],
        received_at: str,
    ) -> None:
        r = await self._client.post(
            "/api/report",
            json={"hostname": hostname, "received_at": received_at, "sessions": sessions},
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code == 401:
            raise AuthError(r.json().get("detail", "unauthorized"))
        r.raise_for_status()
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest agent/tests/test_client.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/server_monitor_agent/client.py agent/tests/test_client.py agent/pyproject.toml
git commit -m "feat(agent): async HTTP client (enroll + report) with bearer auth"
```

### Task 4.7: Main report loop + CLI

**Files:**
- Create: `agent/server_monitor_agent/__main__.py`
- Create: `agent/server_monitor_agent/run.py`
- Create: `agent/tests/test_run_loop.py`

- [ ] **Step 1: Write `agent/tests/test_run_loop.py`**

```python
"""Loop sends a snapshot when state changes; resyncs every Nth tick."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_loop_sends_on_change_and_on_resync(token_file: Path, monkeypatch) -> None:
    from server_monitor_agent import run

    snapshots = [
        [{"device_name": "A", "username": "u", "protocol": "ssh",
          "state": "active", "logon_at": "2030-01-01T11:00:00+00:00"}],
        [{"device_name": "A", "username": "u", "protocol": "ssh",
          "state": "active", "logon_at": "2030-01-01T11:00:00+00:00"}],
        [{"device_name": "A", "username": "u", "protocol": "ssh",
          "state": "active", "logon_at": "2030-01-01T11:00:00+00:00"}],
    ]
    sent: list[list[dict]] = []

    async def fake_collect_async():
        return snapshots.pop(0) if snapshots else []

    class FakeClient:
        async def report(self, *, hostname, token, sessions, received_at):
            sent.append(sessions)
        async def aclose(self): pass

    monkeypatch.setattr(run, "_collect", fake_collect_async)
    await run.run_loop(
        client=FakeClient(),
        hostname="h", token="t",
        interval=0.0, resync_every=2, ticks=3,
    )

    # tick 1: change (initial), tick 2: resync (no change but every 2nd), tick 3: no change → no send.
    assert len(sent) == 2
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest agent/tests/test_run_loop.py -v`
Expected: FAIL.

- [ ] **Step 3: Write `agent/server_monitor_agent/run.py`**

```python
"""Main agent loop: collect → diff → report."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from server_monitor_agent.collect import collect as _sync_collect
from server_monitor_agent.snapshot import Session, is_changed


class _ClientLike(Protocol):
    async def report(self, *, hostname: str, token: str, sessions: list[dict], received_at: str) -> None: ...
    async def aclose(self) -> None: ...


async def _collect() -> list[Session]:
    # Run sync collector in default thread executor so we don't block the loop.
    return await asyncio.to_thread(_sync_collect)


async def run_loop(
    *,
    client: _ClientLike,
    hostname: str,
    token: str,
    interval: float = 5.0,
    resync_every: int = 12,
    ticks: int | None = None,
) -> None:
    """Run the report loop. If `ticks` is None, run forever.

    Transient errors (network blips, monitor restart) are caught here and the
    loop continues. A 401 from the monitor is fatal — the operator must
    re-enroll the agent.
    """
    last: list[Session] = []
    counter = 0
    backoff = interval
    while True:
        counter += 1
        current = await _collect()
        force = counter % resync_every == 0
        if force or is_changed(last, current):
            try:
                await client.report(
                    hostname=hostname, token=token,
                    sessions=list(current),
                    received_at=datetime.now(UTC).isoformat(),
                )
                last = current
                backoff = interval  # success — reset backoff
            except Exception as e:  # noqa: BLE001
                # AuthError (401) bubbles up so the service can stop and surface a clear message.
                from server_monitor_agent.client import AuthError
                if isinstance(e, AuthError):
                    raise
                # Network / 5xx — log to stderr, hold last snapshot, back off.
                print(f"report failed: {e!r}; retrying", file=sys.stderr)
                await asyncio.sleep(min(backoff, 60.0))
                backoff = min(backoff * 2, 60.0)
                if ticks is not None and counter >= ticks:
                    return
                continue
        if ticks is not None and counter >= ticks:
            return
        await asyncio.sleep(interval)
```

Add `import sys` to the top of `run.py` (after `import os`).

> Note: tests pass `interval=0.0` and a finite `ticks` so the loop terminates.

- [ ] **Step 4: Write `agent/server_monitor_agent/__main__.py`**

```python
"""CLI entry: `server-monitor-agent run` and `server-monitor-agent enroll`."""

from __future__ import annotations

import argparse
import asyncio
import socket
import ssl
import sys
from pathlib import Path

from server_monitor_agent.client import Client
from server_monitor_agent.run import run_loop
from server_monitor_agent.token_store import default_token_path, load_token, save_token


def _build_client(base_url: str, ca_bundle: str | None) -> Client:
    verify: bool | str = ca_bundle if ca_bundle else True
    return Client(base_url=base_url, verify=verify)


async def _cmd_enroll(args: argparse.Namespace) -> int:
    client = _build_client(args.monitor_url, args.ca_bundle)
    try:
        token = await client.enroll(
            hostname=args.hostname, enrollment_token=args.enrollment_token
        )
    finally:
        await client.aclose()
    save_token(args.token_file, token)
    print(f"enrolled; token saved to {args.token_file}")
    return 0


async def _cmd_run(args: argparse.Namespace) -> int:
    token = load_token(args.token_file)
    if not token:
        print(f"no token at {args.token_file}; run 'enroll' first", file=sys.stderr)
        return 2
    client = _build_client(args.monitor_url, args.ca_bundle)
    try:
        await run_loop(client=client, hostname=args.hostname, token=token, interval=args.interval)
    finally:
        await client.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="server-monitor-agent")
    p.add_argument(
        "--monitor-url",
        default=os.environ.get("MONITOR_URL", "https://monitor.lan"),
    )
    p.add_argument("--hostname", default=socket.gethostname())
    p.add_argument("--token-file", type=Path, default=default_token_path())
    p.add_argument("--ca-bundle", default=None, help="path to monitor CA cert")
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("enroll")
    pe.add_argument("--enrollment-token", required=True)
    pe.set_defaults(func=_cmd_enroll)

    pr = sub.add_parser("run")
    pr.add_argument("--interval", type=float, default=5.0)
    pr.set_defaults(func=_cmd_run)

    args = p.parse_args(argv)
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run, verify pass**

Run: `.venv/bin/pytest agent/tests/test_run_loop.py -v`
Expected: 1 passed.

- [ ] **Step 6: Run the full agent suite**

Run: `.venv/bin/pytest agent/tests -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add agent/server_monitor_agent/__main__.py agent/server_monitor_agent/run.py \
        agent/tests/test_run_loop.py
git commit -m "feat(agent): main report loop + CLI (enroll/run)"
```

---
## Phase 5 — Agent service wrappers + packaging

These tasks add OS-native service plumbing and PyInstaller specs. The Linux unit can be built and tested locally; the Windows build is exercised via `pyinstaller` only and validated manually in Phase 8.

### Task 5.1: Linux systemd unit

**Files:**
- Create: `agent/installers/server-monitor-agent.service`

- [ ] **Step 1: Write the unit file**

```ini
[Unit]
Description=Server Monitor Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/server-monitor-agent --monitor-url ${MONITOR_URL} --ca-bundle /etc/server-monitor-agent/ca.pem run
EnvironmentFile=-/etc/server-monitor-agent/env
Restart=on-failure
RestartSec=5
User=root
Group=root
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/etc/server-monitor-agent

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Add a tiny smoke test that the unit file is well-formed**

Create `agent/tests/test_systemd_unit.py`:
```python
from pathlib import Path


def test_systemd_unit_has_required_sections() -> None:
    text = Path("agent/installers/server-monitor-agent.service").read_text()
    assert "[Unit]" in text
    assert "[Service]" in text
    assert "[Install]" in text
    assert "ExecStart=" in text
    assert "Restart=on-failure" in text
```

Run: `.venv/bin/pytest agent/tests/test_systemd_unit.py -v`
Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add agent/installers/server-monitor-agent.service agent/tests/test_systemd_unit.py
git commit -m "feat(agent): systemd unit for the Linux service"
```

### Task 5.2: Windows service wrapper

**Files:**
- Create: `agent/server_monitor_agent/service_windows.py`
- Create: `agent/tests/test_service_windows.py`

- [ ] **Step 1: Write `agent/tests/test_service_windows.py`**

```python
"""Smoke test that the Windows service module imports and exposes the required symbols."""

from __future__ import annotations

import sys
import types


def test_module_exposes_service_class(monkeypatch) -> None:
    """We don't run the service here; we only check the API surface.

    pywin32 isn't typically installed on the dev box, so we install fake stubs first.
    """
    fake_w32 = types.ModuleType("win32serviceutil")
    fake_svc = types.ModuleType("win32service")

    class _SvcBase:
        def __init__(self, *_a, **_kw): pass
        @classmethod
        def Install(cls, *a, **kw): pass

    fake_w32.ServiceFramework = _SvcBase
    fake_svc.SERVICE_RUNNING = 0
    fake_svc.SERVICE_STOPPED = 1
    monkeypatch.setitem(sys.modules, "win32serviceutil", fake_w32)
    monkeypatch.setitem(sys.modules, "win32service", fake_svc)
    monkeypatch.setitem(sys.modules, "win32event", types.ModuleType("win32event"))
    monkeypatch.setitem(sys.modules, "servicemanager", types.ModuleType("servicemanager"))

    from server_monitor_agent import service_windows

    assert hasattr(service_windows, "AgentService")
    assert service_windows.AgentService._svc_name_  # noqa: SLF001
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest agent/tests/test_service_windows.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `agent/server_monitor_agent/service_windows.py`**

```python
"""Windows Service wrapper. Imported only on Windows; pywin32 must be installed.

Install / start (run from an admin shell):
    server-monitor-agent-service.exe install
    sc start ServerMonitorAgent
"""

from __future__ import annotations

import asyncio
import os
import sys

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
except ImportError:  # type: ignore[unreachable]
    # Module imported on a non-Windows host (tests, or accidental import).
    win32serviceutil = None  # type: ignore[assignment]


from server_monitor_agent.__main__ import main as cli_main


class AgentService(win32serviceutil.ServiceFramework if win32serviceutil else object):  # type: ignore[misc]
    _svc_name_ = "ServerMonitorAgent"
    _svc_display_name_ = "Server Monitor Agent"
    _svc_description_ = "Reports RDP session activity to the server-monitor service."

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self._stop = win32event.CreateEvent(None, 0, 0, None)
        self._task: asyncio.Task | None = None

    def SvcStop(self):  # noqa: N802
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self._stop)

    def SvcDoRun(self):  # noqa: N802
        servicemanager.LogInfoMsg(f"{self._svc_name_} starting")
        # Delegate to the CLI so settings flow the same way as a manual run.
        sys.argv = ["server-monitor-agent", "run"]
        try:
            cli_main(sys.argv[1:])
        except SystemExit:
            pass


if __name__ == "__main__":
    if win32serviceutil:
        win32serviceutil.HandleCommandLine(AgentService)
    else:
        print("pywin32 is required on Windows", file=sys.stderr)
        raise SystemExit(2)
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest agent/tests/test_service_windows.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/server_monitor_agent/service_windows.py agent/tests/test_service_windows.py
git commit -m "feat(agent): Windows service wrapper (pywin32)"
```

### Task 5.3: PyInstaller specs (Linux + Windows)

**Files:**
- Create: `agent/installers/pyinstaller_linux.spec`
- Create: `agent/installers/pyinstaller_windows.spec`
- Create: `scripts/build_agents.sh`

- [ ] **Step 1: Write `agent/installers/pyinstaller_linux.spec`**

```python
# PyInstaller spec for the Linux agent.
# Build:
#   pyinstaller --clean --distpath ./agents-dist agent/installers/pyinstaller_linux.spec

block_cipher = None

a = Analysis(
    ["../server_monitor_agent/__main__.py"],
    pathex=["../"],
    binaries=[],
    datas=[],
    hiddenimports=[
        "server_monitor_agent.collect_linux",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["server_monitor_agent.collect_windows", "server_monitor_agent.service_windows"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

import platform
arch = "x86_64" if platform.machine() in ("x86_64", "amd64") else "aarch64"

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name=f"agent-linux-{arch}",
    debug=False, bootloader_ignore_signals=False, strip=True, upx=False,
    runtime_tmpdir=None, console=True, target_arch=None, codesign_identity=None,
    entitlements_file=None,
)
```

- [ ] **Step 2: Write `agent/installers/pyinstaller_windows.spec`**

```python
# PyInstaller spec for the Windows agent.
# Build (on a Windows host):
#   pyinstaller --clean --distpath .\agents-dist agent\installers\pyinstaller_windows.spec

block_cipher = None

a = Analysis(
    ["..\\server_monitor_agent\\__main__.py"],
    pathex=["..\\"],
    binaries=[],
    datas=[],
    hiddenimports=[
        "server_monitor_agent.collect_windows",
        "win32ts", "win32serviceutil", "win32service", "win32event", "servicemanager",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["server_monitor_agent.collect_linux"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="agent-windows",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    runtime_tmpdir=None, console=True, target_arch=None, codesign_identity=None,
    entitlements_file=None,
)
```

- [ ] **Step 3: Write `scripts/build_agents.sh`**

```bash
#!/usr/bin/env bash
# Build the Linux agent binary into ./agents-dist/.
# Run on a Windows host with `pyinstaller agent/installers/pyinstaller_windows.spec` to produce the .exe.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/agent/installers"
"$ROOT/.venv/bin/pyinstaller" --clean --distpath "$ROOT/agents-dist" pyinstaller_linux.spec
echo "built into $ROOT/agents-dist"
ls -l "$ROOT/agents-dist"
```

Make executable: `chmod +x scripts/build_agents.sh`.

- [ ] **Step 4: Run the Linux build smoke**

Run: `./scripts/build_agents.sh`
Expected: builds successfully, produces `agents-dist/agent-linux-x86_64`. Run `./agents-dist/agent-linux-x86_64 run --help` to confirm it executes.

- [ ] **Step 5: Update root `.gitignore`**

Confirm `agents-dist/` is not tracked. The existing `agent-dist/` line catches `agent-dist/`; add `agents-dist/` to be safe. Edit `.gitignore` and add under "Build / distribution artifacts":

```
agents-dist/
```

- [ ] **Step 6: Commit**

```bash
git add agent/installers/pyinstaller_linux.spec agent/installers/pyinstaller_windows.spec \
        scripts/build_agents.sh .gitignore
chmod +x scripts/build_agents.sh
git commit -m "build(agent): PyInstaller specs (Linux + Windows) + builder script"
```

---

## Phase 6 — Install scripts

These scripts are downloaded from the monitor (`/install.sh`, `/install.ps1`). They must be safe to pipe into `bash`/PowerShell, idempotent, and produce a working enrolled service.

### Task 6.1: Serve install scripts from the monitor

**Files:**
- Modify: `monitor/app/api/web.py`
- Create: `monitor/app/static/install.sh`
- Create: `monitor/app/static/install.ps1`
- Create: `monitor/tests/test_install_endpoints.py`

- [ ] **Step 1: Write `monitor/tests/test_install_endpoints.py`**

```python
async def test_install_sh_served(client) -> None:
    r = await client.get("/install.sh")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/")
    assert "#!/usr/bin/env bash" in r.text


async def test_install_ps1_served(client) -> None:
    r = await client.get("/install.ps1")
    assert r.status_code == 200
    assert "Install-MonitorAgent" in r.text


async def test_ca_cert_served_when_present(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CADDY_CA_PATH", str(tmp_path / "ca.pem"))
    (tmp_path / "ca.pem").write_text("-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n")
    r = await client.get("/ca.crt")
    assert r.status_code == 200
    assert b"BEGIN CERTIFICATE" in r.content
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_install_endpoints.py -v`
Expected: FAIL.

- [ ] **Step 3: Write `monitor/app/static/install.sh`**

```bash
#!/usr/bin/env bash
# Bootstrap the server-monitor agent on Linux.
# Downloaded via:  curl -fsSL https://<monitor>/install.sh | sudo bash -s -- --token <T> --hostname <H>
set -euo pipefail

MONITOR_URL="${MONITOR_URL:-https://monitor.lan}"
ARCH="$(uname -m)"
HOSTNAME="$(hostname)"
TOKEN=""
TOKEN_FILE="/etc/server-monitor-agent/token"
CA_FILE="/etc/server-monitor-agent/ca.pem"
BINDIR="/usr/local/bin"
UNIT_PATH="/etc/systemd/system/server-monitor-agent.service"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --token) TOKEN="$2"; shift 2 ;;
        --hostname) HOSTNAME="$2"; shift 2 ;;
        --monitor-url) MONITOR_URL="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$TOKEN" ]]; then
    echo "missing --token" >&2; exit 2
fi
if [[ "$EUID" -ne 0 ]]; then
    echo "must run as root (use sudo)" >&2; exit 2
fi

mkdir -p /etc/server-monitor-agent
chmod 700 /etc/server-monitor-agent

# 1. Trust the monitor's CA (one-time, used for all subsequent calls).
echo "==> downloading monitor CA"
curl -kfsSL "${MONITOR_URL}/ca.crt" -o "${CA_FILE}.tmp"
mv "${CA_FILE}.tmp" "$CA_FILE"
chmod 644 "$CA_FILE"

# 2. Download the agent binary using the now-pinned CA.
echo "==> downloading agent binary"
case "$ARCH" in
    x86_64|amd64) ARCH=x86_64 ;;
    aarch64|arm64) ARCH=aarch64 ;;
    *) echo "unsupported arch: $ARCH" >&2; exit 2 ;;
esac
TMPBIN="$(mktemp)"
curl -fsSL --cacert "$CA_FILE" \
    "${MONITOR_URL}/api/agent-binary?os=linux&arch=${ARCH}" -o "$TMPBIN"
chmod +x "$TMPBIN"
install -m 0755 "$TMPBIN" "${BINDIR}/server-monitor-agent"
rm -f "$TMPBIN"

# 3. Pre-register the server with the monitor (so the host appears even if enroll fails later).
"${BINDIR}/server-monitor-agent" \
    --monitor-url "$MONITOR_URL" --ca-bundle "$CA_FILE" --hostname "$HOSTNAME" \
    --token-file "$TOKEN_FILE" \
    enroll --enrollment-token "$TOKEN"

# 4. Install systemd unit.
cat > "$UNIT_PATH" <<UNIT
[Unit]
Description=Server Monitor Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=MONITOR_URL=${MONITOR_URL}
ExecStart=${BINDIR}/server-monitor-agent --monitor-url \${MONITOR_URL} --ca-bundle ${CA_FILE} --hostname ${HOSTNAME} --token-file ${TOKEN_FILE} run
Restart=on-failure
RestartSec=5
User=root
Group=root
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/etc/server-monitor-agent

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now server-monitor-agent
systemctl status server-monitor-agent --no-pager
echo "==> done"
```

- [ ] **Step 4: Write `monitor/app/static/install.ps1`**

```powershell
# Bootstrap the server-monitor agent on Windows.
# Usage:
#   iwr https://<monitor>/install.ps1 -UseBasicParsing | iex
#   Install-MonitorAgent -Token <T> -Hostname <H> [-MonitorUrl https://<monitor>]
function Install-MonitorAgent {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)] [string] $Token,
        [string] $Hostname = $env:COMPUTERNAME,
        [string] $MonitorUrl = "https://monitor.lan"
    )
    if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
            [Security.Principal.WindowsBuiltInRole] "Administrator")) {
        throw "Must run from an elevated PowerShell."
    }

    $InstallDir = "$env:ProgramFiles\server-monitor-agent"
    $DataDir    = "$env:ProgramData\server-monitor-agent"
    $CaPath     = "$DataDir\ca.pem"
    $TokenPath  = "$DataDir\token"
    $ExePath    = "$InstallDir\agent-windows.exe"
    $ServiceName = "ServerMonitorAgent"

    New-Item -ItemType Directory -Force -Path $InstallDir, $DataDir | Out-Null

    # 1. Trust monitor CA (one-time, downloaded over insecure channel on the LAN).
    Write-Host "==> downloading monitor CA"
    [Net.ServicePointManager]::ServerCertificateValidationCallback = {$true}
    Invoke-WebRequest "$MonitorUrl/ca.crt" -UseBasicParsing -OutFile $CaPath
    [Net.ServicePointManager]::ServerCertificateValidationCallback = $null
    Import-Certificate -FilePath $CaPath -CertStoreLocation Cert:\LocalMachine\Root | Out-Null

    # 2. Download agent binary.
    Write-Host "==> downloading agent binary"
    Invoke-WebRequest "$MonitorUrl/api/agent-binary?os=windows" -UseBasicParsing -OutFile $ExePath

    # 3. Lock down ProgramData dir so only SYSTEM + Administrators can read the token.
    $acl = Get-Acl $DataDir
    $acl.SetAccessRuleProtection($true, $false)
    $rules = @(
        New-Object System.Security.AccessControl.FileSystemAccessRule("SYSTEM","FullControl","ContainerInherit,ObjectInherit","None","Allow"),
        New-Object System.Security.AccessControl.FileSystemAccessRule("Administrators","FullControl","ContainerInherit,ObjectInherit","None","Allow")
    )
    $acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) | Out-Null }
    $rules | ForEach-Object { $acl.AddAccessRule($_) }
    Set-Acl $DataDir $acl

    # 4. Enroll.
    & $ExePath --monitor-url $MonitorUrl --hostname $Hostname --token-file $TokenPath enroll --enrollment-token $Token

    # 5. Register and start the service.
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        sc.exe stop $ServiceName | Out-Null
        sc.exe delete $ServiceName | Out-Null
    }
    $binPath = "`"$ExePath`" --monitor-url $MonitorUrl --hostname $Hostname --token-file `"$TokenPath`" run"
    sc.exe create $ServiceName binPath= "$binPath" start= auto displayName= "Server Monitor Agent" | Out-Null
    sc.exe failure $ServiceName reset= 60 actions= restart/5000/restart/5000/restart/5000 | Out-Null
    Start-Service $ServiceName
    Get-Service $ServiceName
    Write-Host "==> done"
}
```

- [ ] **Step 5: Append serving routes to `monitor/app/api/web.py`**

```python
import os as _os
from fastapi.responses import FileResponse, PlainTextResponse


_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/install.sh", response_class=PlainTextResponse)
def install_sh() -> PlainTextResponse:
    return PlainTextResponse(
        (_STATIC_DIR / "install.sh").read_text(),
        media_type="text/x-shellscript; charset=utf-8",
    )


@router.get("/install.ps1", response_class=PlainTextResponse)
def install_ps1() -> PlainTextResponse:
    return PlainTextResponse(
        (_STATIC_DIR / "install.ps1").read_text(),
        media_type="text/plain; charset=utf-8",
    )


@router.get("/ca.crt")
def ca_crt():
    p = Path(_os.environ.get("CADDY_CA_PATH", "/caddy/data/caddy/pki/authorities/local/root.crt"))
    if not p.exists():
        raise HTTPException(status_code=404, detail="CA not provisioned yet")
    return FileResponse(p, media_type="application/x-x509-ca-cert", filename="ca.crt")
```

- [ ] **Step 6: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_install_endpoints.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add monitor/app/static/install.sh monitor/app/static/install.ps1 \
        monitor/app/api/web.py monitor/tests/test_install_endpoints.py
git commit -m "feat(monitor): serve install.sh, install.ps1, and ca.crt"
```

---

## Phase 7 — Deployment

### Task 7.1: Monitor Dockerfile

**Files:**
- Create: `monitor/Dockerfile`
- Create: `monitor/.dockerignore`

- [ ] **Step 1: Write `monitor/Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install '.[dev]'

COPY app /app/app

EXPOSE 8000
ENV DB_PATH=/data/server-monitor.sqlite

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Write `monitor/.dockerignore`**

```
__pycache__/
*.pyc
.venv/
tests/
```

- [ ] **Step 3: Smoke-build the image**

Run: `docker build -t server-monitor:dev monitor/`
Expected: build completes.

- [ ] **Step 4: Smoke-run**

Run:
```bash
docker run --rm -e DB_PATH=/tmp/test.sqlite -p 8000:8000 server-monitor:dev &
sleep 2
curl -fsS http://127.0.0.1:8000/ | head -c 200
docker kill $(docker ps -q --filter ancestor=server-monitor:dev) 2>/dev/null || true
```
Expected: HTML page returned.

- [ ] **Step 5: Commit**

```bash
git add monitor/Dockerfile monitor/.dockerignore
git commit -m "build(monitor): Dockerfile + .dockerignore"
```

### Task 7.2: docker-compose.yml + Caddyfile

**Files:**
- Create: `docker-compose.yml`
- Create: `Caddyfile`

- [ ] **Step 1: Write `Caddyfile`**

```caddyfile
{
    # Internal CA + automatic self-signed leaf certs. Agents pin /caddy/data/caddy/pki/authorities/local/root.crt.
    auto_https disable_redirects
}

{$MONITOR_HOST:monitor.lan}:443 {
    tls internal

    # Path to the file the monitor exposes for /ca.crt is taken from the env in the
    # monitor container (CADDY_CA_PATH points at the same volume mounted there).
    encode gzip

    handle /sse {
        reverse_proxy monitor:8000 {
            transport http {
                read_timeout 0
                response_header_timeout 0
            }
            flush_interval -1
        }
    }

    handle {
        reverse_proxy monitor:8000
    }
}

# Plain HTTP listener for the install.sh/install.ps1 first-bootstrap fetch of /ca.crt.
:80 {
    handle /ca.crt {
        reverse_proxy monitor:8000
    }
    handle {
        redir https://{$MONITOR_HOST:monitor.lan}{uri} 308
    }
}
```

- [ ] **Step 2: Write `docker-compose.yml`**

```yaml
services:
  monitor:
    build: ./monitor
    environment:
      MONITOR_HOST: ${MONITOR_HOST:-monitor.lan}
      DISPLAY_TZ: ${DISPLAY_TZ:-UTC}
      ENROLLMENT_TOKEN_TTL: ${ENROLLMENT_TOKEN_TTL:-3600}
      BCRYPT_COST: ${BCRYPT_COST:-10}
      DB_PATH: /data/server-monitor.sqlite
      AGENT_DIST_DIR: /agents-dist
      CADDY_CA_PATH: /caddy/data/caddy/pki/authorities/local/root.crt
    volumes:
      - ./data:/data
      - ./agents-dist:/agents-dist:ro
      - caddy_data:/caddy/data:ro
    expose:
      - "8000"
    restart: unless-stopped

  caddy:
    image: caddy:2
    environment:
      MONITOR_HOST: ${MONITOR_HOST:-monitor.lan}
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - monitor
    restart: unless-stopped

volumes:
  caddy_data:
  caddy_config:
```

- [ ] **Step 3: Smoke `docker compose up`**

Run:
```bash
mkdir -p data agents-dist
echo "MONITOR_HOST=monitor.lan" > .env
docker compose up -d
sleep 5
curl -ksSL https://localhost/ -o /tmp/idx.html && grep -q "Server Monitor" /tmp/idx.html && echo OK
docker compose logs --tail 50 monitor caddy
docker compose down
```
Expected: `OK` printed; no errors in logs that aren't TLS handshake noise.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml Caddyfile
git commit -m "build(deploy): docker-compose with monitor + Caddy reverse proxy"
```

---

## Phase 8 — End-to-end smoke + docs

### Task 8.1: Dev seed script + manual smoke procedure

**Files:**
- Create: `scripts/dev_seed.py`
- Create: `docs/superpowers/specs/manual-smoke.md`

- [ ] **Step 1: Write `scripts/dev_seed.py`**

```python
"""Seed the monitor with a fake enrolled server + a synthetic session.

Usage (from repo root, after `docker compose up -d` and `pip install -e monitor[dev]`):
    python scripts/dev_seed.py
"""

from __future__ import annotations

import os
import sys

import httpx


BASE = os.environ.get("MONITOR_BASE", "https://localhost")
VERIFY = os.environ.get("MONITOR_CA", False)  # set to a CA path to verify


def main() -> int:
    with httpx.Client(base_url=BASE, verify=VERIFY) as c:
        r = c.post("/api/admin/server", json={"hostname": "demo-srv", "os": "linux"})
        r.raise_for_status()
        token_pending = r.json()["enrollment_token"]

        r = c.post("/api/enroll", json={"hostname": "demo-srv", "enrollment_token": token_pending})
        r.raise_for_status()
        agent_token = r.json()["agent_token"]

        c.post(
            "/aliases",
            json={"device_name": "LAPTOP-DEMO", "alias": "demo user"},
        )

        c.post(
            "/api/report",
            json={
                "hostname": "demo-srv",
                "received_at": "2030-01-01T12:00:00+00:00",
                "sessions": [
                    {
                        "device_name": "LAPTOP-DEMO",
                        "username": "shared",
                        "protocol": "ssh",
                        "state": "active",
                        "logon_at": "2030-01-01T11:55:00+00:00",
                    }
                ],
            },
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        print("seeded; visit", BASE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write `docs/superpowers/specs/manual-smoke.md`**

```markdown
# Manual smoke test

After `docker compose up -d` and a successful `./scripts/build_agents.sh`:

1. **Seed:** `python scripts/dev_seed.py` (uses `MONITOR_BASE=https://localhost`, `verify=False` by default).
2. **Dashboard:** open `https://localhost/` (browser will warn — internal CA). You should see `demo-srv` with one session "demo user (LAPTOP-DEMO)" marked `active`.
3. **Aliases page:** `https://localhost/aliases` shows `LAPTOP-DEMO → demo user`. Edit the alias inline; refresh; persists.
4. **Server detail:** click `demo-srv`; the day grid renders 48 cells. Click an open cell, pick a name from the autocomplete (the alias you set is in the list), confirm. The cell flips to "booked".
5. **Conflict:** book the same cell again from another browser tab → "someone just took this slot" toast (or 409 in network tab) and grid refresh.
6. **Stale agent:** stop the seed script. Wait 60–90 s. Card shows "agent offline" badge.

## Windows-only verification (must be run on a real Windows host)

These cannot be exercised on the Linux dev box:
- `Install-MonitorAgent` from a fresh PowerShell-as-admin (target: a clean Windows Server 2019/2022 VM).
- The `ServerMonitorAgent` Windows Service starts at boot and survives reboot.
- An RDP session from a personal device shows up on the dashboard with the device's `WTSClientName` (the personal device's hostname).
- A disconnected RDP session shows as `disconnected`, not `active`.

If any of these fail, file a bug before tagging a release.
```

- [ ] **Step 3: Commit**

```bash
git add scripts/dev_seed.py docs/superpowers/specs/manual-smoke.md
git commit -m "docs: dev seed script + manual smoke procedure"
```

### Task 8.2: README polish

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the "Repo layout (planned)" section with the actual layout, and add a Quick start**

Replace the `## Repo layout (planned)` section in `README.md` with:

```markdown
## Quick start

```bash
# 1. Build the Linux agent binary (Windows .exe must be built on a Windows host).
./scripts/build_agents.sh

# 2. Spin up the monitor + Caddy.
cp .env.example .env
docker compose up -d

# 3. Add a server: open https://<MONITOR_HOST>/enroll and follow the install command.
# 4. Watch the dashboard at https://<MONITOR_HOST>/.
```

See [`docs/superpowers/specs/manual-smoke.md`](docs/superpowers/specs/manual-smoke.md) for the smoke-test procedure.

## Repo layout

```
.
├── README.md
├── .env.example
├── docker-compose.yml
├── Caddyfile
├── docs/superpowers/
│   ├── specs/
│   └── plans/
├── monitor/
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── app/
│       ├── api/        # agents, web, bookings, aliases, sse routers
│       ├── core/       # pure logic — db, tokens, sessions, bookings, aliases, servers, stale, clock
│       ├── templates/  # Jinja2 + HTMX
│       └── static/     # CSS, JS, install.sh, install.ps1
├── agent/
│   ├── pyproject.toml
│   ├── server_monitor_agent/
│   │   ├── collect_linux.py / collect_windows.py / collect.py
│   │   ├── client.py / run.py / __main__.py
│   │   └── service_windows.py
│   └── installers/     # systemd unit, PyInstaller specs
└── scripts/            # build helpers, dev seed
```
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README quick start + final repo layout"
```

### Task 8.3: Final full-suite check

- [ ] **Step 1: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all green across `monitor/tests` and `agent/tests`.

- [ ] **Step 2: Lint**

Run: `.venv/bin/ruff check monitor agent scripts`
Expected: no errors. Fix any inline.

- [ ] **Step 3: docker-compose smoke**

Run: `docker compose up -d && sleep 5 && curl -ksS https://localhost/ | grep -q 'Server Monitor' && echo OK && docker compose down`
Expected: `OK`.

- [ ] **Step 4: Commit any lint fixes**

```bash
git status
git commit -am "chore: lint fixes" || true
```

---

## Self-review checklist (post-write)

The plan covers each in-scope item from the spec:

- Live dashboard with current sessions, login times, alias display → Tasks 3.1, 3.2, 4.x (collectors), 2.4–2.5 (report ingestion).
- Public, editable alias map → Tasks 1.5, 2.7, 3.3.
- 30-min booking, 7-day horizon, day view per server → Tasks 1.4, 2.6, 3.4.
- Single-command onboarding (Linux + Windows) → Tasks 5.1, 5.2, 5.3, 6.1.
- No secrets in repo → Phase 0 `.gitignore` + design enforcement; Phase 7 mounts secrets via volumes.
- Docker-compose deployment → Tasks 7.1, 7.2.
- Live updates via SSE → Tasks 2.2, 3.6.
- Stale-agent detection → Task 2.8.
- Re-enrollment / replacement → Task 1.7 (`reset_server`) — **gap:** no UI button yet. Add Task 8.4 below.
- Windows-only smoke → Task 8.1.

### Task 8.4: Reset-server admin action (closes the gap from self-review)

**Files:**
- Modify: `monitor/app/api/web.py`
- Modify: `monitor/app/templates/enroll.html`
- Create: `monitor/tests/test_web_reset.py`

- [ ] **Step 1: Write `monitor/tests/test_web_reset.py`**

```python
async def test_reset_button_regenerates_enrollment_token(client) -> None:
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    sid = r.json()["server_id"]
    enroll = r.json()["enrollment_token"]
    # complete enrollment so we have a real agent_token_hash to clear
    await client.post("/api/enroll", json={"hostname": "srv-a", "enrollment_token": enroll})

    r = await client.post(f"/enroll/{sid}/reset")
    assert r.status_code == 200
    body = r.text
    assert "install" in body.lower()
    assert enroll not in body  # token should be a new one
```

- [ ] **Step 2: Run, verify failure**

Run: `.venv/bin/pytest monitor/tests/test_web_reset.py -v`
Expected: FAIL — endpoint missing.

- [ ] **Step 3: Append to `monitor/app/api/web.py`**

```python
from app.core.servers import reset_server


@router.post("/enroll/{server_id}/reset", response_class=HTMLResponse)
def reset_server_endpoint(
    server_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    row = _server_or_404(conn, server_id)
    token = reset_server(conn, server_id=server_id, ttl_seconds=settings.enrollment_token_ttl)
    return _TEMPLATES.TemplateResponse(
        request,
        "enroll.html",
        {
            "display_tz": settings.display_tz,
            "monitor_host": settings.monitor_host,
            "rows": [dict(r) for r in _list_servers(conn)],
            "command": {"hostname": row["hostname"], "os": row["os"], "token": token},
            "selected_os": row["os"],
        },
    )
```

- [ ] **Step 4: Add a Reset button in `monitor/app/templates/enroll.html`**

In the existing `<tbody>` loop, replace the trailing empty cell with:

```html
{% for r in rows %}
<tr>
    <td>{{ r.hostname }}</td>
    <td>{{ r.os }}</td>
    <td>{% if r.enrolled %}enrolled{% else %}pending{% endif %}</td>
    <td><small data-iso="{{ r.last_seen_at }}"></small></td>
    <td>
        <form method="post" action="/enroll/{{ r.id }}/reset"
              onsubmit="return confirm('Generate a new enrollment token? The old agent will stop working.');">
            <button type="submit" class="secondary">Reset</button>
        </form>
    </td>
</tr>
{% endfor %}
```

(Update the `<thead>` row to add an empty `<th></th>`.)

- [ ] **Step 5: Run, verify pass**

Run: `.venv/bin/pytest monitor/tests/test_web_reset.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add monitor/app/api/web.py monitor/app/templates/enroll.html \
        monitor/tests/test_web_reset.py
git commit -m "feat(monitor): admin Reset button regenerates enrollment token"
```

---

## End of plan

After all phases complete:
- All `monitor/tests` and `agent/tests` pass under `pytest -q`.
- `docker compose up -d` produces a working monitor on `https://${MONITOR_HOST}/`.
- A fresh server can be onboarded with a single copy-paste install command.
- The repository contains no secrets; `.gitignore` was in place from commit 1.
- Manual smoke (`docs/superpowers/specs/manual-smoke.md`) — including the Windows-only items — is the final gate before tagging a release.
