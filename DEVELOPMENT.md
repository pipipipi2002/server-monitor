# DEVELOPMENT.md

A tour of how `server-monitor` is built. The README covers what it does and how to run it; this doc explains *why* each piece exists, what the underlying mechanics are, and where to look (or change) when contributing.

The audience is a developer comfortable with Python and the basics of HTTP who hasn't built a Python+FastAPI+SQLite app before. We'll spend extra time on the bits that "feel like magic" the first time you see them — editable installs, PyInstaller bundling, Caddy's automatic TLS, HTMX-driven UIs, and the trust-on-first-use bootstrap that makes single-command server onboarding work.

---

## Table of contents

1. [Tech stack at a glance](#1-tech-stack-at-a-glance)
2. [Project layout & why](#2-project-layout--why)
3. [`pip install -e .[dev]` — editable installs explained](#3-pip-install--edev--editable-installs-explained)
4. [The monitor: how a request flows through the stack](#4-the-monitor-how-a-request-flows-through-the-stack)
5. [The agent: collect → diff → report, with retries](#5-the-agent-collect--diff--report-with-retries)
6. [The onboarding flow: serving install scripts and binaries over HTTPS](#6-the-onboarding-flow-serving-install-scripts-and-binaries-over-https)
7. [Database design & the reconciliation pattern](#7-database-design--the-reconciliation-pattern)
8. [Real-time updates: SSE + HTMX](#8-real-time-updates-sse--htmx)
9. [Testing strategy](#9-testing-strategy)
10. [PyInstaller: turning Python into a single binary](#10-pyinstaller-turning-python-into-a-single-binary)
11. [Caddy & TLS: the reverse proxy in this stack](#11-caddy--tls-the-reverse-proxy-in-this-stack)
12. [Configuration plumbing](#12-configuration-plumbing)
13. [Adding a new feature — worked example](#13-adding-a-new-feature--worked-example)
14. [Gotchas you'll hit](#14-gotchas-youll-hit)

---

## 1. Tech stack at a glance

| Layer | Choice | Why |
|---|---|---|
| Language (server + agent) | **Python 3.12** | Single language across both halves of the system. 3.12's typing (`list[T]`, `\| None`) and `from __future__ import annotations` keep code modern without runtime cost. |
| Web framework | **FastAPI 0.110+** | Pydantic-based validation, async-native, OpenAPI for free. Lighter than Django for this scope; more structure than Flask. |
| ASGI server | **Uvicorn** | Standard FastAPI runner. With `[standard]` extras you get uvloop + httptools for performance. |
| Schema validation | **Pydantic v2** | Used at HTTP boundaries (request bodies). The non-FastAPI internals use `dataclass` and `TypedDict` instead — Pydantic in hot loops is overkill. |
| Templating | **Jinja2** | Server-rendered HTML. Autoescape on by default. |
| Frontend dynamism | **HTMX + Alpine.js** | No build step, no bundler, no framework. HTMX swaps server-rendered HTML fragments; Alpine adds tiny bits of client state for the booking modal. |
| Frontend styling | **Pico.css** | Class-less CSS via CDN; we add ~50 lines of `app.css` on top. No Tailwind/Bootstrap config to maintain. |
| Database | **SQLite (raw `sqlite3`)** | Single-host, low-write workload. No ORM — schema is small enough that hand-written SQL is clearer. WAL mode handles concurrent reads while a write is in flight. |
| Password / token hashing | **bcrypt** | Industry-standard adaptive hash. We hash agent tokens at rest so a database leak doesn't yield plaintext credentials. |
| HTTP client (agent) | **httpx** | Async, typed, supports `MockTransport` for clean tests without monkey-patching `requests`. |
| Real-time | **Server-Sent Events** (SSE) | One-way push (server → browser) is all we need; SSE is far simpler than WebSockets, runs over plain HTTP/2, and reconnects on its own. |
| Tests | **pytest + pytest-asyncio** | Async fixtures + `httpx.AsyncClient` against the in-process FastAPI app. No live server needed for integration tests. |
| Packaging | **PyInstaller 6** | Bundles the agent (interpreter + stdlib + deps) into one executable per OS. The target server doesn't need Python installed. |
| Reverse proxy | **Caddy 2** | Automatic TLS via its internal CA — agents trust a single root that Caddy generates on first start. Zero TLS config to write. |
| Container runtime | **Docker + docker-compose** | Two services (`monitor`, `caddy`) and two volumes. The monitor host runs nothing else. |
| Service supervision | **systemd** (Linux), **Windows Services** (Win) | Native to each OS; the agent ships as a long-running service that auto-restarts on failure. |
| Windows API access | **pywin32** | Wraps `WTSEnumerateSessions` / `WTSQuerySessionInformation` so we can read the actual RDP source device name (the personal laptop) instead of just "shared user logged in". |

The principle behind the choices: **prefer boring, well-understood tools that minimize the surface area we'd have to debug.** Every layer above could be swapped out without rewriting more than its immediate neighbors.

---

## 2. Project layout & why

```
.
├── monitor/                          # The dashboard server
│   ├── pyproject.toml                # Its own package: 'server-monitor'
│   ├── Dockerfile
│   └── app/
│       ├── main.py                   # FastAPI app factory + lifespan
│       ├── config.py                 # Settings (env vars)
│       ├── deps.py                   # FastAPI dependencies (DB, settings)
│       ├── api/                      # HTTP routers
│       │   ├── agents.py             # /api/enroll, /api/report, /api/agent-binary
│       │   ├── bookings.py           # /bookings (CRUD)
│       │   ├── aliases.py            # /aliases (POST), /api/aliases (GET)
│       │   ├── web.py                # / (dashboard), /server/<id>, /enroll, etc.
│       │   └── sse.py                # /sse + the in-process Broadcaster
│       ├── core/                     # PURE LOGIC — no FastAPI imports here
│       │   ├── db.py                 # connect() + schema bootstrap
│       │   ├── tokens.py             # bcrypt-backed token hash/verify
│       │   ├── clock.py              # UTC helpers + slot math
│       │   ├── bookings.py           # validation + insert + conflict detection
│       │   ├── aliases.py            # device → alias map
│       │   ├── sessions.py           # apply_snapshot(): reconcile reports → DB
│       │   ├── servers.py            # enrollment lifecycle + auth
│       │   └── stale.py              # detect agents that stopped reporting
│       ├── templates/                # Jinja2 + HTMX
│       └── static/                   # app.css, app.js, install.sh, install.ps1
│
├── agent/                            # The Python agent (one per monitored host)
│   ├── pyproject.toml                # Its own package: 'server-monitor-agent'
│   ├── server_monitor_agent/
│   │   ├── __main__.py               # CLI: 'enroll' and 'run' subcommands
│   │   ├── collect.py                # Dispatches to OS-specific collector
│   │   ├── collect_linux.py          # Parses `who -u`
│   │   ├── collect_windows.py        # Calls pywin32's WTSEnumerateSessions
│   │   ├── snapshot.py               # Session type + diff helper
│   │   ├── client.py                 # httpx wrapper for /api/enroll, /api/report
│   │   ├── token_store.py            # Reads/writes the agent token from disk
│   │   ├── run.py                    # The main collect→diff→report loop
│   │   └── service_windows.py        # pywin32 ServiceFramework wrapper
│   └── installers/
│       ├── server-monitor-agent.service     # systemd unit for Linux
│       ├── pyinstaller_linux.spec
│       └── pyinstaller_windows.spec
│
├── pyproject.toml                    # Workspace root (pytest + ruff config only)
├── docker-compose.yml                # monitor + caddy services
├── Caddyfile                         # TLS + reverse proxy rules
├── .env.example                      # Operator-facing config
├── scripts/
│   ├── build_agents.sh               # PyInstaller wrapper
│   └── dev_seed.py                   # Populate test data for manual smoke
└── docs/superpowers/                 # Spec + plan
```

### Why two pyproject.tomls?

Because monitor and agent have **different runtime dependencies and target environments.** The monitor is a server (FastAPI, bcrypt, Jinja2); the agent is a tiny client (httpx, optionally pywin32). Bundling them into one package would force the agent build to drag FastAPI along — bloating the PyInstaller binary by ~30 MB.

The workspace `pyproject.toml` at the repo root contains only tooling config (`pytest`, `ruff`) — no dependencies. It's a "monorepo of two packages" pattern.

### Why the `core/` vs `api/` split?

The `core/` modules are **pure logic** — they take a `sqlite3.Connection` and primitives, return data structures, and never know about HTTP. The `api/` modules are **adapters** — they unpack the request, call into core, format the response. This means:

- Tests for booking validation don't need to spin up a FastAPI app.
- Swapping FastAPI for, say, a CLI later wouldn't touch the core.
- The reasoning load when reading any one file stays low.

You'll see the same pattern in the agent: `snapshot.py` is pure logic; `client.py` is the I/O adapter.

---

## 3. `pip install -e .[dev]` — editable installs explained

This is the bit that "feels like magic" the first time. Let's walk through exactly what happens.

### The literal command

From the repo root:

```bash
.venv/bin/pip install -e 'monitor[dev]'
```

(On Windows: `.\.venv\Scripts\pip install -e .\monitor[dev]`. The path syntax differs but the semantics are identical.)

We're telling pip:

- `-e` / `--editable`: install in **editable mode** (more on that below).
- `monitor`: the path to the package. Pip looks here for a `pyproject.toml` to read.
- `[dev]`: install the optional dependency group named `dev` in addition to the regular ones.

### What "editable" actually does

A normal `pip install <package>` does this:

1. Resolves dependencies.
2. Downloads (or builds) wheel files.
3. Unpacks the wheels into `site-packages/` — which is just a directory of `.py` files inside your venv.

After that, the source code in `site-packages/` is a **copy.** Edits to your repo don't affect the installed version until you reinstall.

`pip install -e .` does this instead:

1. Resolves dependencies (same as before).
2. Downloads/installs *those* dependencies normally.
3. For *your* package, instead of copying files, it writes a small "pointer" into `site-packages/` that tells Python "when someone imports `app`, look in `/home/marvinp/projects/server-monitor/monitor/app/` instead."

The pointer is a [PEP 660](https://peps.python.org/pep-0660/) editable wheel. In practice it's a `.pth` file or a `__editable__.<name>.pth` file. You can see it with:

```bash
ls .venv/lib/python3.12/site-packages/ | grep -i editable
cat .venv/lib/python3.12/site-packages/__editable__.server_monitor-0.1.0.pth
# /home/marvinp/projects/server-monitor/monitor
```

That `.pth` file is read by Python at startup and prepended to `sys.path`. So when our code says `from app.core.db import connect`, Python:

1. Walks `sys.path`.
2. Finds `/home/marvinp/projects/server-monitor/monitor` (from the `.pth`).
3. Finds `app/core/db.py` inside it.
4. Imports it.

**Edits to source are immediately picked up** because the import resolves to live source files, not a cached copy. That's why we can change `core/bookings.py`, save, and the next test run picks up the change without any reinstall step.

### The `[dev]` part — dependency extras

Open `monitor/pyproject.toml`:

```toml
[project]
name = "server-monitor"
dependencies = [
  "fastapi>=0.110",
  "uvicorn[standard]>=0.29",
  "jinja2>=3.1",
  "pydantic>=2.6",
  "bcrypt>=4.1",
  "python-multipart>=0.0.9",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-asyncio>=0.23",
  "httpx>=0.27",
  "ruff>=0.4",
]
```

The `dependencies` list is what *every* user gets — what's needed to run the monitor. `optional-dependencies.dev` is an extra group that's only installed if you ask for it.

`pip install -e 'monitor[dev]'` means: install monitor + the contents of `dependencies` + the contents of `optional-dependencies.dev`. The square brackets are the syntax for "extras."

You can also chain: `pip install -e 'monitor[dev]' -e 'agent[dev]'` installs both packages in editable mode with their dev extras. That's how a fresh dev environment gets bootstrapped.

### Why we want this

1. **Fast iteration.** Edit code, run tests, see results — no reinstall step.
2. **Ergonomic imports.** `from app.config import Settings` works whether you're running tests, uvicorn, or `python -c`. Without the editable install, you'd need to twiddle `PYTHONPATH` or `sys.path`.
3. **Tooling integration.** Pytest discovers tests by importing them. Without editable mode, pytest would find tests at `monitor/tests/test_db.py` but `from app.core.db import connect` inside the test would fail because `app` isn't on the path. Editable install makes it Just Work.
4. **The console-script entry point** for the agent (`server-monitor-agent = "server_monitor_agent.__main__:main"` in `agent/pyproject.toml`) creates a `.venv/bin/server-monitor-agent` shell script during install. After `pip install -e 'agent[dev]'`, you can run `.venv/bin/server-monitor-agent --help` from anywhere — and that command imports source live from `agent/server_monitor_agent/`, so changes are immediate.

### What the second command does (`pyinstaller ...`)

```bash
.venv/bin/pyinstaller --clean --distpath ./agents-dist agent/installers/pyinstaller_linux.spec
```

This is **not** about installing — it's about **packaging**. PyInstaller takes the agent source plus its dependencies plus a Python interpreter and bundles them into a single executable file. Covered in §10 below.

### A subtle gotcha: `from X import Y` creates a local binding

```python
# core/bookings.py
from app.core.clock import now

def create_booking(...):
    if start_at < now():
        raise BookingError(...)
```

When tests want to mock the clock, they have to patch `app.core.bookings.now`, **not** `app.core.clock.now`. The reason: `from X import Y` copies the reference into the importing module's namespace. After import, `bookings.now` is its own name pointing at the same function. Patching `clock.now` doesn't update `bookings.now`.

We deal with this in `monitor/tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr("app.core.clock.now", lambda: FIXED_NOW)
    for module_name in ("app.core.bookings", "app.core.aliases",
                        "app.core.servers", "app.core.stale"):
        # ... patch each consumer's local binding too
```

Whenever you add a new `core/` module that imports `now` (or `now_iso`) from `clock`, **add it to that tuple,** or your tests will silently use the real wall clock and start failing in a year.

---

## 4. The monitor: how a request flows through the stack

Let's trace what happens when a browser POSTs to `/bookings`.

```
Browser → Caddy (TLS, :443) → monitor:8000 → uvicorn → FastAPI app → router → handler
```

### Caddy

The reverse proxy. Browser hits `https://monitor.lan/bookings`; Caddy terminates TLS using its self-signed internal cert and forwards to `monitor:8000` over plain HTTP inside the docker-compose network. See §11 for TLS details.

### Uvicorn

An ASGI server. It takes the FastAPI app object exported as `app.main:app` and runs it inside an event loop. Uvicorn handles the raw socket → HTTP → Python coroutine layer.

### FastAPI app construction

In `monitor/app/main.py`:

```python
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
```

A factory function (instead of a top-level `app = FastAPI()`) is essential for testability: the `client` fixture in `conftest.py` calls `build_app()` against a temp SQLite, which would be impossible with a singleton instantiated at import time.

The `lifespan` context manager starts a background asyncio task on app startup (the stale-server detector) and cancels it on shutdown.

### Router → handler

`bookings.router` is registered. When the request matches `POST /bookings`, FastAPI invokes:

```python
@router.post("/bookings", status_code=status.HTTP_201_CREATED)
async def create(
    body: CreateBookingRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    ...
```

Two things to notice:

1. **`body: CreateBookingRequest`** — FastAPI sees a Pydantic model in the signature, parses the JSON body, validates against the schema (`server_id: int`, `start_at: str`, etc.), and passes the validated object in. Validation failures auto-respond as HTTP 422 with structured error detail. We didn't write any validation code.

2. **`conn: sqlite3.Connection = Depends(get_db)`** — FastAPI's [dependency injection](https://fastapi.tiangolo.com/tutorial/dependencies/). `get_db` is a generator function that yields a connection; FastAPI calls it for every request. Our implementation in `deps.py` returns a process-singleton (SQLite handles its own write locking, so one shared connection is fine for this scale), but the `Depends()` machinery means handlers don't import `deps` directly — they declare what they need and FastAPI wires it up.

The handler delegates to `app.core.bookings.create_booking`, which does the validation + INSERT, then publishes an SSE event so other browsers' dashboards refresh.

### Response

FastAPI sees the handler returned `{"id": 42}`. It serializes to JSON, sets the status code from `@router.post(..., status_code=201)`, and hands bytes back to uvicorn → Caddy → browser.

### Why the indirection is worth it

If you're new to FastAPI, this might feel like a lot of layers. The payoff:

- **Type-safety.** Wrong types in the request body never reach handler code.
- **OpenAPI docs.** Visit `/docs` (Swagger UI) — every endpoint is documented automatically from the type hints.
- **Dependency reuse.** `get_db` is shared by 15+ handlers. Adding "rate limiting" or "audit logging" is a one-line dependency you Depends() in.
- **Async by default.** Handlers can `await` external calls without blocking the event loop.

---

## 5. The agent: collect → diff → report, with retries

The agent is a single Python process running as a service on each monitored host. It does one thing in a loop:

```python
# agent/server_monitor_agent/run.py (abridged)
async def run_loop(*, client, hostname, token, interval=5.0, resync_every=12, ...):
    last: list[Session] = []
    counter = 0
    backoff = interval
    while True:
        counter += 1
        current = await _collect()                 # OS-specific
        force = counter % resync_every == 0        # Full snapshot every 60s
        if force or is_changed(last, current):
            try:
                await client.report(
                    hostname=hostname, token=token,
                    sessions=list(current),
                    received_at=datetime.now(UTC).isoformat(),
                )
                last = current
                backoff = interval
            except AuthError:
                raise                              # Fatal — re-enroll needed
            except Exception as e:
                # Network/5xx — exponential backoff capped at 60s
                print(f"report failed: {e!r}; retrying", file=sys.stderr)
                await asyncio.sleep(min(backoff, 60.0))
                backoff = min(backoff * 2, 60.0)
                continue
        await asyncio.sleep(interval)
```

Three design choices here:

1. **Resync every 12th tick.** Even when nothing changes, we send a full snapshot every minute. This way the monitor never builds up drift if it missed a delta (e.g. monitor restart).
2. **Exponential backoff.** A network blip shouldn't trigger 5/sec retries. We start at the normal interval (5s) and double up to 60s on consecutive failures, resetting on success.
3. **401 is fatal.** If the monitor says "I don't recognize your token," the agent doesn't keep trying — the operator needs to re-enroll. The exception bubbles up and crashes the service, which systemd/SCM logs prominently.

### Per-OS data collection

`collect.py` is a thin dispatcher:

```python
def collect() -> list[Session]:
    if sys.platform == "win32":
        return _collect_windows()
    return _collect_linux()
```

#### Linux (`collect_linux.py`)

We shell out to `who -u` and parse with a regex:

```
alice    pts/0        2030-01-01 11:55 (192.168.1.42)   ← remote SSH
alice    tty1         2030-01-01 09:00                  ← local console
```

`who` is in coreutils; it's on every Linux box. We avoid `loginctl` (systemd-specific) for portability.

#### Windows (`collect_windows.py`)

This is where pywin32 earns its keep. Native Windows APIs let us read **the source device name** of an RDP session — i.e., the name of the personal laptop someone is connecting *from*, not just "shared user is logged in":

```python
import win32ts
handle = win32ts.WTS_CURRENT_SERVER_HANDLE
for session_id, _name, state in win32ts.WTSEnumerateSessions(handle):
    client_name = win32ts.WTSQuerySessionInformation(handle, session_id, win32ts.WTSClientName)
    user        = win32ts.WTSQuerySessionInformation(handle, session_id, win32ts.WTSUserName)
    ...
```

`WTSClientName` is the value the team's laptops report when they RDP in — `DESKTOP-ALICE-LAPTOP` or whatever Windows decided on first boot. That's the value the alias map keys on.

We also handle disconnected sessions: when someone closes their RDP window without logging off, `WTSConnectState` returns `WTSDisconnected` (4), and we mark the session `disconnected` instead of `active`. Disconnected sessions still hold the single-user RDP slot, so they're meaningful to display.

### Why we mock pywin32 in tests

The dev box is Linux. pywin32 won't even install there (it's gated by `sys_platform == 'win32'` in `agent/pyproject.toml`). Tests in `agent/tests/test_collect_windows.py` inject a fake `win32ts` module via `monkeypatch.setitem(sys.modules, "win32ts", fake)` *before* importing `collect_windows`. The collector code then calls our fake API and we assert the produced `Session` shape.

This catches structural bugs on the dev box; **actual Windows verification is a manual step** before each release (see `docs/superpowers/specs/manual-smoke.md`).

---

## 6. The onboarding flow: serving install scripts and binaries over HTTPS

This is the part the user asked the most about — let's go slow.

### The problem

A fresh server has nothing installed. We want a single command, copy-pasted by an operator, to:

1. Download a shell script.
2. Trust the monitor's TLS certificate.
3. Download the agent binary.
4. Register with the monitor (exchange a one-shot enrollment token for a long-lived agent token).
5. Install the binary as a service and start it.

All in under 2 minutes. With no shared secret beyond the one-shot token printed on the enrollment page.

### The chicken-and-egg

The monitor uses TLS via Caddy's **internal CA** — Caddy mints its own root cert on first start and uses it to sign per-domain leaf certs. That works great for browsers (you click through one warning, then trust it forever) but creates a problem for headless agents: they need to trust Caddy's root CA *before* they can validate any TLS connection to the monitor.

If we required all agents to be pre-provisioned with a CA cert, we'd lose the "single command" property. So we use a **trust-on-first-use** bootstrap: download the CA over an *insecure* connection once, pin it to disk, then verify everything subsequently.

### The bootstrap dance

#### Step 1 — operator runs the install command

```bash
curl -fsSL https://monitor.lan/install.sh | sudo bash -s -- --token <T>
```

`curl` validates the TLS cert by default. **This step works because the operator's browser already accepted the cert** — when they hit the `/enroll` page to generate the token, they'd have clicked through a "self-signed certificate" warning, which on Linux means... nothing, actually. `curl` doesn't share the browser's trust store. So technically this first `curl` call would fail TLS verification.

In practice we work around it because the install command starts with `https://...` and `curl` is invoked from an interactive shell. If verification fails, the operator copies the command, adds `-k` once, or installs the CA manually. The dance below makes that one-time pain unnecessary:

#### Step 2 — fetch the CA cert (insecure first connection)

Inside `install.sh`:

```bash
curl -kfsSL "${MONITOR_URL}/ca.crt" -o "${CA_FILE}.tmp"
mv "${CA_FILE}.tmp" "$CA_FILE"
```

`-k` tells curl to skip certificate verification *for this single request*. We download the CA cert and pin it to `/etc/server-monitor-agent/ca.pem`.

This is the only operation in the entire flow that doesn't validate TLS. It's a calculated risk: an attacker with access to the LAN at the exact moment an operator runs the install command could MITM and substitute a fake CA. The trust model accepts this — the LAN is trusted (see README "Trust model").

#### Step 3 — fetch the agent binary, verifying the just-pinned CA

```bash
curl -fsSL --cacert "$CA_FILE" \
    "${MONITOR_URL}/api/agent-binary?os=linux&arch=${ARCH}" -o "$TMPBIN"
```

`--cacert` tells curl to use *only* this CA bundle for verification. From here on, anything served by the monitor is verifiable.

The binary itself is built by `scripts/build_agents.sh` and lives in `agents-dist/agent-linux-x86_64`. Inside the docker-compose mount, that's `/agents-dist/`. The monitor's `/api/agent-binary` endpoint serves it:

```python
@router.get("/agent-binary")
def agent_binary(os: str, arch: str = "x86_64") -> FileResponse:
    dist = Path(_os.environ.get("AGENT_DIST_DIR", "/agents-dist"))
    name = "agent-linux-x86_64" if os == "linux" else "agent-windows.exe"
    p = dist / name
    if not p.exists():
        raise HTTPException(status_code=404, detail="binary not built yet")
    return FileResponse(p, filename=name, media_type="application/octet-stream")
```

`FileResponse` is a FastAPI/Starlette helper that streams the file from disk in chunks (no full read into memory) and sets `Content-Length`, `Last-Modified`, and other headers correctly. It's the right tool for "send a file the user should download" — better than reading the whole file and returning bytes, which would block the worker.

The `application/octet-stream` media type is the universal "binary blob, save to disk" hint.

#### Step 4 — enroll

```bash
"${BINDIR}/server-monitor-agent" \
    --monitor-url "$MONITOR_URL" --ca-bundle "$CA_FILE" --hostname "$HOSTNAME" \
    --token-file "$TOKEN_FILE" \
    enroll --enrollment-token "$TOKEN"
```

The agent calls `POST /api/enroll` with the one-shot token. The monitor validates it (against a bcrypt hash), generates a fresh long-lived agent token, hashes that too, and returns the plaintext to the agent — **the only opportunity to read the plaintext.** The agent writes it to `/etc/server-monitor-agent/token` with `chmod 0600`. From here on, every report includes `Authorization: Bearer <agent-token>`, validated by bcrypt-comparing against the stored hash.

#### Step 5 — install + start the service

`install.sh` writes a systemd unit at `/etc/systemd/system/server-monitor-agent.service` (a heredoc'd version of `agent/installers/server-monitor-agent.service` with template variables substituted), then `systemctl enable --now`. The agent starts and immediately reports.

### How `install.sh` itself is served

```python
# monitor/app/api/web.py
@router.get("/install.sh", response_class=PlainTextResponse)
def install_sh() -> PlainTextResponse:
    return PlainTextResponse(
        (_STATIC_DIR / "install.sh").read_text(),
        media_type="text/x-shellscript; charset=utf-8",
    )
```

The script lives at `monitor/app/static/install.sh` (so it's bundled with the package and accessible inside the container). The endpoint reads it and returns as `text/x-shellscript`. We could have used FastAPI's `StaticFiles` mount, but a hand-written endpoint lets us:

- Set the right media type (`StaticFiles` would guess from the extension).
- Maybe later inject `MONITOR_URL` into the script if we want to remove the `--token` argument.
- Have a clean URL (`/install.sh` instead of `/static/install.sh`).

### Three different ways FastAPI serves files — when to use which

| Need | Tool | Why |
|---|---|---|
| Static assets behind a path prefix (CSS, JS, images) | `app.mount("/static", StaticFiles(directory=...))` | Streams files efficiently, sets cache headers, handles HEAD requests. Mounted as a sub-app. |
| A single file with custom logic (auth check, content-type override) | `FileResponse(path, ...)` | Returns a streaming response with the right `Content-Length`. Use for downloads, agent binary, generated CA cert. |
| Inline text content (small scripts, generated content) | `PlainTextResponse(text, ...)` | Materializes the text in memory. Good for ≤1 MB; not for streaming a video file. |

We use all three. `/static/app.css` → `StaticFiles`. `/api/agent-binary` and `/ca.crt` → `FileResponse`. `/install.sh` and `/install.ps1` → `PlainTextResponse`.

### Why the install script doesn't authenticate beyond the token

The script itself is unauthenticated — anyone on the LAN can `curl https://monitor.lan/install.sh`. The script *body* contains no secrets; it's a generic bootstrap that takes a token as an argument. The token is the secret, generated per-server by the operator clicking "Add server" on the enrollment page. Without a valid token, the agent can't enroll, and the monitor returns 401 immediately.

This is intentional: making the script itself secret would just be security theater (anyone on the LAN can already see Caddy's TLS handshake metadata, and the script is plaintext shell anyway).

---

## 7. Database design & the reconciliation pattern

The schema is in `monitor/app/core/db.py`. Five tables: `servers`, `sessions`, `aliases`, `bookings`, `reports`. SQLite-specific touches:

```python
c.execute("PRAGMA journal_mode=WAL")        # Concurrent reads + one writer
c.execute("PRAGMA foreign_keys=ON")         # Enforce FK constraints
c.execute("PRAGMA busy_timeout=5000")       # Wait up to 5s if locked
```

WAL is the killer feature for our workload: writers don't block readers, and one writer blocks others for milliseconds. With 20 agents reporting every 5s + a handful of dashboards open, contention isn't measurable.

### Why no ORM?

A SQLite schema this small (5 tables, ~30 columns) is clearer as hand-written SQL. Adding SQLAlchemy would mean:

- Defining `Mapped[...]` columns that mirror the DDL.
- Reasoning about session/transaction boundaries.
- A second mental model layered on top of the actual SQL.

Trade-off: queries are scattered across `core/*.py` instead of being typed methods on a model class. We accept that.

### The reconciliation pattern (`apply_snapshot`)

This is the trickiest piece of monitor logic. The agent posts a list of currently-active sessions; the monitor needs to figure out:

- Which sessions are **new** (insert a row).
- Which existing sessions are **still there** (update `last_seen_at`, maybe `state`).
- Which existing sessions **disappeared** (mark `ended_at = received_at`).

```python
def apply_snapshot(conn, *, server_id, sessions, received_at):
    open_rows = conn.execute(
        "SELECT id, device_name, state FROM sessions WHERE server_id=? AND ended_at IS NULL",
        (server_id,),
    ).fetchall()
    open_by_device = {r["device_name"]: r for r in open_rows}
    seen = set()

    for s in sessions:
        device = s["device_name"]
        seen.add(device)
        existing = open_by_device.get(device)
        if existing is None:
            conn.execute("INSERT INTO sessions (...) VALUES (...)")
            diff.added.append(...)
        elif existing["state"] != s["state"]:
            conn.execute("UPDATE sessions SET state=?, last_seen_at=? WHERE id=?")
            diff.changed.append(...)
        else:
            conn.execute("UPDATE sessions SET last_seen_at=? WHERE id=?")

    for device, row in open_by_device.items():
        if device not in seen:
            conn.execute("UPDATE sessions SET ended_at=?, last_seen_at=? WHERE id=?")
            diff.ended.append(...)

    conn.execute("UPDATE servers SET last_seen_at=? WHERE id=?")
    return diff
```

The function returns a `Diff` (added/changed/ended), which the API layer broadcasts via SSE. Browsers refresh just the affected card. Tests use this returned diff to assert exact behavior without needing to query the DB.

Two correctness invariants:

1. **A logical session is identified by `(server_id, device_name)` while `ended_at IS NULL`.** Once `ended_at` is set, that row is "history" and a new connection from the same device gets a new row.
2. **The function is idempotent given the same input.** Calling it twice with the same snapshot produces the same DB state (last_seen_at advances, but no spurious inserts/ends).

---

## 8. Real-time updates: SSE + HTMX

### Server-Sent Events vs WebSockets

Both push data from server to browser. Differences:

|  | SSE | WebSockets |
|---|---|---|
| Direction | server → client | bidirectional |
| Protocol | HTTP/1.1 or HTTP/2 | upgrade to a separate protocol |
| Reconnection | automatic by browser | manual |
| Encoding | text only | text or binary |
| Server complexity | trivial (just a long-lived response) | non-trivial |

We only need server → client, so SSE is dramatically simpler. The endpoint:

```python
@router.get("/sse")
async def sse_endpoint() -> StreamingResponse:
    queue = broadcaster.subscribe()
    return StreamingResponse(_stream(queue), media_type="text/event-stream")

async def _stream(queue):
    yield b": connected\n\n"          # SSE comment, ignored by client
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=15.0)
            yield f"data: {json.dumps(event)}\n\n".encode("utf-8")
        except TimeoutError:
            yield b": ping\n\n"        # Heartbeat to keep the connection alive
```

The browser-side is two lines:

```javascript
const es = new EventSource("/sse");
es.onmessage = e => {
    const evt = JSON.parse(e.data);
    window.dispatchEvent(new CustomEvent("sm:event", { detail: evt }));
};
```

`EventSource` is built into every browser. It auto-reconnects with exponential backoff if the connection drops.

### The Broadcaster

In-memory fan-out. `broadcaster.publish(event)` puts the event on every subscriber's queue. The endpoint loops over the queue and emits each event as an SSE message.

```python
class Broadcaster:
    def __init__(self, queue_max=64):
        self._subs: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=64)
        self._subs.append(q)
        return q

    async def publish(self, event):
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer; drop the oldest to keep flowing.
                q.get_nowait()
                q.put_nowait(event)
```

Slow-consumer handling matters: if a browser tab is backgrounded and the JS event loop falls behind, we don't want every other subscriber to slow down too. **Drop oldest** keeps the steady-state freshness of the SSE stream.

### HTMX: server-rendered fragments

HTMX makes "AJAX without writing JS" a first-class pattern. Mark up a button with `hx-*` attributes and the library handles the request:

```html
<form hx-post="/aliases" hx-target="closest tr" hx-swap="outerHTML">
    <input name="device_name" value="DESKTOP-A">
    <input name="alias" value="alice">
    <button type="submit">Save</button>
</form>
```

When this form submits:

1. HTMX intercepts the click, prevents the default form post.
2. POSTs the form data to `/aliases` with header `HX-Request: true`.
3. The server returns an HTML fragment (not a full page) — in our case, the updated `<tr>` for that alias.
4. HTMX uses `hx-target` (`closest tr`) and `hx-swap` (`outerHTML`) to splice the fragment into the DOM.

The server detects HTMX by checking `HX-Request` and returns the partial:

```python
@router.post("/aliases")
async def upsert(request, hx_request: str = Header(default=None, alias="HX-Request"), ...):
    ...
    if hx_request == "true":
        return _TEMPLATES.TemplateResponse(
            request, "_partials/alias_row.html", {"row": ...}
        )
    return RedirectResponse(url="/aliases", status_code=303)
```

If a browser without JS submits the same form, the server falls back to a 303 redirect to `/aliases`. So the page works *without* HTMX too — progressive enhancement, not a hard JS dependency.

### How the dashboard refreshes on SSE events

`monitor/app/static/app.js`:

```javascript
window.addEventListener("sm:event", e => {
    const evt = e.detail;
    if (["report", "alias.updated", "server.online", "server.offline"].includes(evt.type)) {
        if (document.getElementById("server-grid")) refreshGrid();
    }
});

function refreshGrid() {
    fetch("/?fragment=grid", { headers: { "HX-Request": "true" } })
        .then(r => r.text())
        .then(html => {
            document.getElementById("server-grid").outerHTML = html;
        });
}
```

When an SSE event arrives, we re-fetch just the dashboard grid (`/?fragment=grid` returns only the `_partials/server_grid.html` partial) and replace the DOM node. At ≤20 servers this is negligible; at 200+ we'd do per-card swaps via `hx-trigger="sse:report"` directly.

---

## 9. Testing strategy

### What we test where

| Layer | Test approach |
|---|---|
| `core/*` (pure logic) | Unit tests with a temp SQLite via the `conn` fixture. Fast (< 1 ms each). |
| `api/*` (HTTP endpoints) | Integration tests against the in-process FastAPI app via the `client` fixture. ~10 ms each. |
| Templates | Integration tests that GET the page and assert key strings. We don't unit-test Jinja — we test rendered output. |
| Agent collectors | Linux: parse fixture text. Windows: inject a fake `win32ts` module via `monkeypatch.setitem`. |
| Agent run loop | Inject a `FakeClient` that records calls; drive the loop with a finite `ticks` parameter. |
| Cross-platform glue (PyInstaller, Caddy, install scripts) | Smoke tested manually. |

### The `client` fixture

```python
@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("BCRYPT_COST", "4")          # speed up tests
    monkeypatch.setenv("ENROLLMENT_TOKEN_TTL", "3600")

    from app.deps import reset_db_for_tests
    from app.main import build_app

    reset_db_for_tests()                            # drop any cached connection
    app = build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    reset_db_for_tests()
```

This is the killer fixture for an integration-tested FastAPI app. `ASGITransport` lets `httpx.AsyncClient` talk to the FastAPI app **in the same process** — no socket, no port. Tests run as fast as plain function calls but exercise the full HTTP stack including routing, dependency injection, validation, and middleware.

### `fixed_clock` — the monkey-patched clock

Tests assert dates like `"2030-01-01"` because those are unambiguously in the future at the time of writing. But they're also ~4 years past the 7-day booking horizon relative to wall time. Solution: pin `now()` to late 2029 in tests so spec dates are valid:

```python
@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr("app.core.clock.now", lambda: FIXED_NOW)
    for module_name in ("app.core.bookings", ...):
        # Patch each consumer's local binding (see §3 gotcha)
```

Result: tests that assert "the booking I just inserted has start_at == 2030-01-01T14:00:00" stay green forever, regardless of when they run.

### Why we removed `tests/__init__.py`

Both `monitor/tests/` and `agent/tests/` have a `conftest.py`. With `__init__.py` files turning each into a package, both conftests would resolve to the module path `tests.conftest` — pytest crashes with "Plugin already registered."

Two fixes:

- Delete the `__init__.py` files (we did this).
- Use `--import-mode=importlib`, which uses fully-qualified file paths instead of package names. We added this to the workspace `pyproject.toml` for belt-and-braces.

### TDD as an enforced loop

The plan demanded **red → green → commit** for every task: write the failing test first, run it, confirm it fails for the right reason, then implement until green. Subagents that skipped the red step were caught by the spec reviewer. The discipline pays off — every behavior in the codebase is explicitly tested, and the test names document the requirements.

---

## 10. PyInstaller: turning Python into a single binary

The agent target machines don't have Python installed, and we don't want them to. PyInstaller bundles:

- The CPython interpreter.
- The standard library.
- Our `server_monitor_agent` package.
- All third-party deps (httpx, pywin32 on Windows).
- A small bootloader.

…into a single executable. Run `./agents-dist/agent-linux-x86_64` and it acts exactly like running `python -m server_monitor_agent`.

### The spec file

`agent/installers/pyinstaller_linux.spec`:

```python
a = Analysis(
    ["../server_monitor_agent/__main__.py"],         # entry point
    pathex=["../"],                                  # where to find packages
    hiddenimports=["server_monitor_agent.collect_linux"],
    excludes=["server_monitor_agent.collect_windows", "server_monitor_agent.service_windows"],
    ...
)
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(pyz, a.scripts, a.binaries, ...,
          name=f"agent-linux-{arch}",
          strip=True, upx=False)
```

`Analysis` walks the import graph from `__main__.py` and finds every module needed. `hiddenimports` lists modules imported dynamically (e.g. `import_module(...)`) that PyInstaller can't see statically. `excludes` strips Windows-only modules out of the Linux build.

### Why we have two specs

The agent imports either `collect_linux` or `collect_windows` based on `sys.platform`, but both files exist in the source tree. Without `excludes`, PyInstaller would bundle pywin32 into the Linux binary (failing because pywin32 doesn't import on Linux) — or vice versa. Splitting into two specs lets each binary include only what it needs.

### Why Windows builds must run on Windows

PyInstaller cross-platform packaging is essentially impossible. The bootloader is a native binary; pywin32 is a stack of native DLLs; the bundling step copies the host system's interpreter. Running PyInstaller on Linux to produce a Windows .exe doesn't work even with Wine. The pragmatic answer: build Windows artifacts on a Windows host (a CI runner or a VM).

### What ends up in the binary

```bash
./agents-dist/agent-linux-x86_64 --help     # works without any Python on the system
file ./agents-dist/agent-linux-x86_64       # ELF 64-bit LSB pie executable
ls -lh ./agents-dist/agent-linux-x86_64     # ~12 MB
```

The 12 MB includes the entire CPython interpreter and stdlib. That's a tax we pay for "no runtime dependency on the target." A Go agent would be 8 MB; we'd pay the cost of a second language and lose code reuse with the monitor.

---

## 11. Caddy & TLS: the reverse proxy in this stack

### What a reverse proxy does, generally

A reverse proxy sits between clients and your application server. The browser thinks it's talking to *the proxy*; the proxy forwards requests to the actual app over a private network. Common reasons to have one:

- **TLS termination.** Decrypt HTTPS once at the edge, talk to the app over plain HTTP inside docker.
- **Routing.** Multiple apps on one host (e.g. `grafana.local`, `prometheus.local`, `monitor.local`) all share the same public ports 80/443; the proxy looks at the `Host` header and dispatches to the right backend.
- **Compression / buffering / caching.** Centralize gzip, response buffering, and HTTP/2 instead of teaching each app to do them.
- **Security headers.** Add HSTS, CSP, X-Frame-Options once at the edge.
- **Observability.** Single chokepoint for access logs, request metrics, and tracing.

For a single-app deployment like this, only the first two reasons are load-bearing — but they're load-bearing enough that running without a proxy would mean teaching the FastAPI app to terminate TLS, which is more work than just running Caddy in a container.

### Why we picked Caddy

We had three reasonable choices: nginx, Traefik, Caddy. Each terminates TLS and reverse-proxies. The differentiators that mattered for this project:

| | nginx | Traefik | Caddy |
|---|---|---|---|
| Config style | Imperative directives | YAML / labels / providers | Caddyfile (line-oriented, readable) |
| Automatic TLS | Manual / Certbot side-car | ACME built-in | ACME *and* internal CA built-in |
| Internal-CA mode | No (`mkcert` workaround) | No (`mkcert` workaround) | **`tls internal` — one line** |
| Default config size for our use | ~30 lines | ~50 lines + provider setup | **~25 lines** |
| Streaming/SSE friendly defaults | Needs explicit `proxy_buffering off` | Mostly ok by default | Needs explicit `flush_interval -1` |

**`tls internal` was the killer feature.** This deployment targets a LAN with no real domain (`monitor.lan` won't pass an ACME challenge). Caddy generates its own root CA on first start, signs a leaf for `${MONITOR_HOST}`, and renews automatically — no operator action, no manual `openssl` invocation, no `mkcert` install. Browsers click through one warning and trust it forever.

### How Caddy is used here

The whole config is in `Caddyfile`:

```caddyfile
{
    auto_https disable_redirects
}

{$MONITOR_HOST:monitor.lan}:443 {
    tls internal
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

:80 {
    handle /ca.crt { reverse_proxy monitor:8000 }
    handle { redir https://{$MONITOR_HOST:monitor.lan}{uri} 308 }
}
```

Reading top-to-bottom:

- The global `{ auto_https disable_redirects }` keeps Caddy from overriding the manual port-80 handler we want for the CA-cert bootstrap.
- The 443 site block does TLS termination via the internal CA and proxies everything to `monitor:8000` (the FastAPI container, addressable by service name on the docker network).
- The `/sse` handle disables read timeout, response-header timeout, and per-buffer flushing — without these, SSE messages would be buffered for ~30 s before the browser sees them. **This is the main subtlety to know if you ever change the proxy.**
- Port 80 serves only `/ca.crt` (so install scripts can fetch it before they trust anything) and redirects everything else to HTTPS.

### `tls internal` and `/ca.crt` — how the cert ends up on the agent

Caddy's internal CA artifacts live at `/data/caddy/pki/authorities/local/root.crt` inside the Caddy container. Persisted via the `caddy_data` named volume. Two consumers of that file:

- **Caddy itself,** to sign leaf certs.
- **The monitor container,** to expose the CA over HTTP at `/ca.crt`.

The monitor mounts the volume **read-only**:

```yaml
# docker-compose.yml
monitor:
  volumes:
    - caddy_data:/caddy/data:ro
  environment:
    CADDY_CA_PATH: /caddy/data/caddy/pki/authorities/local/root.crt
```

The endpoint just streams the file:

```python
@router.get("/ca.crt")
def ca_crt():
    p = Path(_os.environ.get("CADDY_CA_PATH", "/caddy/data/caddy/pki/authorities/local/root.crt"))
    if not p.exists():
        raise HTTPException(status_code=404, detail="CA not provisioned yet")
    return FileResponse(p, media_type="application/x-x509-ca-cert", filename="ca.crt")
```

Available on **port 80** (so install scripts can fetch it before they have any way to validate TLS) and on port 443 (for browsers, after they've already trusted the cert via the warning click-through). The trust-on-first-use bootstrap is the same as described in §6.

### Reverse-proxy concerns specific to this app

| Concern | Where addressed |
|---|---|
| TLS termination + auto-renewal | Caddy `tls internal` |
| `Host` header routing (only one app today) | Single site block in Caddyfile |
| Long-lived SSE streams | `read_timeout 0`, `flush_interval -1` |
| Compression | `encode gzip` (skipped automatically for SSE because Caddy detects `text/event-stream`) |
| HTTPS-only enforcement | Port 80 redirects everything except `/ca.crt` |
| Agent CA bootstrap | `/ca.crt` exposed by the monitor, available on port 80 |

### Coexisting with an existing reverse proxy (Traefik / nginx / Apache)

If your machine already runs Traefik, nginx, or another proxy on ports 80/443 — for example, you have multiple `*.local` hosts already routed via `traefik.local` and you want to add `monitor.local` to that — the cleanest setup is to **remove Caddy entirely** and let your existing proxy handle TLS and routing.

Two reverse proxies don't share host ports; running Caddy on alt ports behind Traefik works but adds a useless hop.

#### Removing Caddy from docker-compose

Delete the whole `caddy` service plus the `caddy_data` and `caddy_config` volumes. Then expose the monitor on the network your existing proxy uses.

#### The Traefik shape (replace `traefik` with whatever your existing network is)

```yaml
services:
  monitor:
    build: ./monitor
    environment:
      MONITOR_HOST: monitor.local
      DISPLAY_TZ: ${DISPLAY_TZ:-UTC}
      ENROLLMENT_TOKEN_TTL: ${ENROLLMENT_TOKEN_TTL:-3600}
      BCRYPT_COST: ${BCRYPT_COST:-10}
      DB_PATH: /data/server-monitor.sqlite
      AGENT_DIST_DIR: /agents-dist
      # CADDY_CA_PATH no longer relevant — see TLS section below
    volumes:
      - ./data:/data
      - ./agents-dist:/agents-dist:ro
    networks:
      - default
      - traefik
    labels:
      - "traefik.enable=true"
      - "traefik.docker.network=traefik"
      - "traefik.http.routers.monitor.rule=Host(`monitor.local`)"
      - "traefik.http.routers.monitor.entrypoints=websecure"
      - "traefik.http.routers.monitor.tls=true"
      - "traefik.http.services.monitor.loadbalancer.server.port=8000"
    restart: unless-stopped

networks:
  traefik:
    external: true
    name: <traefik_default-or-whatever-yours-is-named>
```

Key bits:

- The monitor joins **two** networks: `default` (the internal docker-compose network for this stack) and `traefik` (shared with the Traefik stack).
- `traefik.docker.network=traefik` tells Traefik which network to use when reaching this container — important if it's on multiple.
- `loadbalancer.server.port=8000` is the FastAPI port inside the container.
- The `Host()` rule should match what your DNS / `/etc/hosts` already routes to the host — same pattern as `prometheus.local`, `grafana.local` in your existing setup.

#### TLS strategy with an existing proxy — two scenarios

**Scenario A: your proxy uses real CA-trusted certs** (Let's Encrypt, or your org's PKI rolled out to every device).

This is the simpler case. Agents validate the cert against the system trust store; they don't need to pin a custom CA. The install script can drop the CA dance entirely:

```bash
# Edit monitor/app/static/install.sh — remove these lines:
#   curl -kfsSL "${MONITOR_URL}/ca.crt" -o "${CA_FILE}.tmp"
#   ...
# And drop the --cacert flag from subsequent curls and from --ca-bundle in the systemd unit.
```

The `/ca.crt` endpoint becomes dead code — it returns 404 because `CADDY_CA_PATH` doesn't exist. That's fine; nobody calls it. You can also remove the route from `monitor/app/api/web.py` if you want to be tidy.

**Scenario B: your proxy uses self-signed certs** (the `*.local` development pattern).

Agents still need to pin a CA — but now it's **your proxy's** CA, not Caddy's. Two sub-options:

1. Have your proxy expose its CA at `/ca.crt` (Traefik supports this via a static-file middleware or you can run a tiny sidecar). The agent install scripts work as written — they download `ca.crt`, pin it, use `--cacert` thereafter.
2. Pre-distribute the CA to each monitored server out of band (Ansible, MDM, manual scp). Drop the CA-fetch step from `install.sh`, hardcode `--ca-bundle /path/to/your/ca.pem`.

Scenario A is much less work; if you're considering this seriously, getting a real cert via Let's Encrypt or your internal PKI for `monitor.local` is the highest-leverage move.

#### SSE through Traefik

Traefik 2/3 doesn't buffer responses with `Content-Type: text/event-stream` — the FastAPI handler sets that header, so SSE works out of the box. If you observe browser-side staleness, the usual culprits are:

- A buffering middleware applied to the router (e.g. compression with a too-large buffer). Move SSE to a router without that middleware, or exclude `/sse` from compression.
- An entry-point timeout cutting the connection. Set `entryPoints.websecure.transport.respondingTimeouts.idleTimeout=0` (Traefik static config) or the equivalent label.

The semantics we need from any reverse proxy for SSE:

- **No response buffering** for `text/event-stream`.
- **No idle-timeout cut-off** (the heartbeat is every 15 s, but operators sometimes pause an SSH tunnel for longer).
- **Forward `Content-Type` and `Cache-Control: no-cache`** as-is.

#### nginx instead of Traefik

Same idea, different config. Site block sketch:

```nginx
server {
    listen 443 ssl http2;
    server_name monitor.local;
    ssl_certificate     /etc/ssl/monitor.local.crt;
    ssl_certificate_key /etc/ssl/monitor.local.key;

    location /sse {
        proxy_pass         http://monitor:8000;
        proxy_http_version 1.1;
        proxy_buffering    off;          # critical for SSE
        proxy_read_timeout 24h;
        proxy_send_timeout 24h;
    }

    location / {
        proxy_pass http://monitor:8000;
    }
}
```

`proxy_buffering off` is the nginx equivalent of Caddy's `flush_interval -1` — without it, SSE events sit in the kernel buffer until it fills.

### When you'd keep Caddy

If you're standing up server-monitor on a host that has nothing else running, keeping Caddy is the lowest-friction path: one extra container, zero certificate work. The `tls internal` mode has no realistic competitor for that use case. Swapping it out is a deliberate choice you make when you're folding the stack into an existing reverse-proxy deployment.

---

## 12. Configuration plumbing

Three layers, each scoped to its level of stability:

| Layer | What | Where |
|---|---|---|
| **Build-time** | Python deps, runtime version | `pyproject.toml` |
| **Deploy-time** | hostname, TZ, bcrypt cost | `.env` (read by docker-compose, passed as env vars to containers) |
| **Run-time** | enrollment tokens, agent tokens | Generated dynamically; stored only in SQLite/disk |

### `Settings` — the env reader

```python
@dataclass
class Settings:
    monitor_host: str = ""
    display_tz: str = ""
    enrollment_token_ttl: int = 0
    db_path: str = ""

    def __post_init__(self):
        self.monitor_host = os.environ.get("MONITOR_HOST", "monitor.lan")
        ...
```

A dataclass instead of Pydantic Settings because we want envs read **at instantiation time**, not at module load. That lets test fixtures use `monkeypatch.setenv(...)` and then call `Settings()` to get the patched values. (Pydantic v1's `BaseSettings` cached values too aggressively for this; v2's `pydantic-settings` works fine but adds a dep we don't otherwise need.)

### Per-request scoping

`get_settings()` is a FastAPI dependency that returns a fresh `Settings()` per request. So tests that monkeypatch env vars get isolated state. Production cost is negligible — reading 4 env vars per request is microseconds.

---

## 13. Adding a new feature — worked example

Say you want to add an "all sessions, ever" history page at `/history`. Where do you go?

1. **Domain logic** — query in `core/sessions.py`:

   ```python
   def list_sessions_paged(conn, *, server_id=None, limit=50, before_id=None):
       sql = "SELECT * FROM sessions"
       params = []
       conds = []
       if server_id: conds.append("server_id=?"); params.append(server_id)
       if before_id: conds.append("id<?");        params.append(before_id)
       if conds: sql += " WHERE " + " AND ".join(conds)
       sql += " ORDER BY id DESC LIMIT ?"
       params.append(limit)
       return conn.execute(sql, params).fetchall()
   ```

   Test: `monitor/tests/test_sessions_logic.py` — write a TDD failing test first.

2. **HTTP route** — endpoint in `api/web.py`:

   ```python
   @router.get("/history", response_class=HTMLResponse)
   def history_page(request, server_id: int | None = None,
                    before: int | None = None,
                    conn=Depends(get_db), settings=Depends(get_settings)):
       rows = list_sessions_paged(conn, server_id=server_id, before_id=before, limit=50)
       return _TEMPLATES.TemplateResponse(request, "history.html",
           {"display_tz": settings.display_tz, "rows": [dict(r) for r in rows]})
   ```

3. **Template** — `monitor/app/templates/history.html`. Extend `base.html`, render a table.

4. **Test** — `monitor/tests/test_web_history.py`:

   ```python
   async def test_history_lists_rows(client):
       # ... set up a server + session via /api/admin/server + /api/report
       body = (await client.get("/history")).text
       assert "LAPTOP-A" in body
   ```

5. **Nav link** — add `<li><a href="/history">History</a></li>` to `templates/base.html`.

6. **Update README** if user-visible.

Time investment: ~30 minutes for someone familiar with the codebase, including TDD red-green cycle. This is the value of the layered structure.

---

## 14. Gotchas you'll hit

### "ModuleNotFoundError: No module named 'app'"

You forgot to `pip install -e 'monitor[dev]'` in your venv, or you're running pytest from the wrong directory. Run from the repo root.

### "Plugin already registered" from pytest

You added `__init__.py` to `monitor/tests/` or `agent/tests/`. Don't — the workspace `pyproject.toml` uses `--import-mode=importlib` precisely so we can have two unrelated test packages.

### Tests that pass alone but fail together

Almost certainly the `_FIRST_SEEN` cache in `collect_windows.py` or a similar module-level state. Add `monkeypatch.setattr(...)` to clear it in the relevant fixture, or use `@pytest.fixture(autouse=True)` to reset state.

### Booking tests start failing in 2030

You added a new core module that imports `now` from `clock`, but didn't add it to the `fixed_clock` fixture's tuple in `monitor/tests/conftest.py`. Add it; tests pass.

### Caddy doesn't start on first `docker compose up`

Port 80 or 443 is already bound. Check `ss -ltnp | grep -E ':(80|443) '`. Common culprits: a host nginx, IIS on Windows, or a previous compose stack.

### `pywin32` won't install in the venv

It's gated by `sys_platform == 'win32'`. On Linux/macOS it's silently skipped — that's correct. Don't try to force it.

### The agent reports session every tick even when nothing changes

That's the resync_every parameter — every 12th tick (60s) is a forced full snapshot. By design.

### Browsers cache the old `app.js` after a change

Add a `?v=2` query string to the script tag in `base.html`, or hard-refresh with Cmd-Shift-R / Ctrl-F5.

### Spec dates fail in tests

Either you removed the `fixed_clock` fixture from conftest, or you didn't use `from datetime import UTC, datetime` to build the dates, or you used `datetime.now(UTC)` in a test that expects mocked time. See §3 and §9.

---

## Where to look for what

| Question | Look at |
|---|---|
| What's the data model? | `monitor/app/core/db.py` |
| How does X validate Y? | Search `monitor/app/core/` for the right module |
| What does the dashboard display? | `monitor/app/templates/dashboard.html` + `_partials/server_card.html` |
| How does the agent identify a session? | `monitor/app/core/sessions.py` + `agent/server_monitor_agent/collect_windows.py` (or `_linux`) |
| Why does this test pass? | `monitor/tests/conftest.py` + the fixture used |
| How is X deployed? | `docker-compose.yml` + `monitor/Dockerfile` + `Caddyfile` |
| Why was decision X made? | `docs/superpowers/specs/2026-05-10-server-monitor-design.md` |
| What was the implementation order? | `docs/superpowers/plans/2026-05-10-server-monitor-implementation.md` |

When in doubt, the **spec** is the source of truth for *what* and *why*; the **plan** records the *how* in step-by-step form, including all the small fixes we made along the way.
