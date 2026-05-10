---
title: Server Monitor — Design Spec
date: 2026-05-10
status: draft (awaiting review)
---

# Server Monitor — Design Spec

## 1. Overview

A small, self-hosted web application that shows who is currently logged in (RDP/SSH) on each of ~5–20 Windows and Linux servers shared by a team. Solves the "kicked out of RDP" problem caused by Windows Server's single-interactive-session limit. Also lets team members reserve 30-minute slots on a server to coordinate access ahead of time.

The application is hosted in Docker on the team's internal network. Adding a new server takes one install command and finishes in under two minutes.

### 1.1 In-scope

- Live dashboard of every monitored server: hostname, OS, current sessions, login time, RDP source device name, idle/disconnected state, agent-online indicator.
- Public, editable mapping from raw device names to human-friendly aliases ("alice's laptop").
- Per-server day timeline of 30-minute slots, bookable up to 7 days ahead.
- Single-command agent install for Windows Server (2016+) and Linux (any with systemd).
- Single-host Docker deployment via `docker compose up`.

### 1.2 Out-of-scope (explicit non-goals)

- Per-user authentication on the web UI (the team is trusted on the LAN).
- Enforcement of bookings — agents do not log anyone off; bookings are purely informational.
- Defense against malicious LAN insiders.
- Multi-tenant SaaS use, account billing, role-based access control.
- Detailed performance metrics (CPU/RAM/disk) — out of scope for v1, may be added later.

### 1.3 Decisions captured during brainstorming

| Decision | Choice |
| --- | --- |
| Data collection | Agent-push (small service per server, HTTPS to monitor). |
| Scale & topology | ~5–20 servers, all on internal LAN/VPN. |
| Web UI auth | None. Open on LAN. Booking attribution by typed/picked name. |
| Booking semantics | Informational, honor-system. No agent-side enforcement. |
| Refresh cadence | Near real-time (~5 s) via SSE. |
| Tech stack | Python + FastAPI + SQLite, HTMX + Alpine.js frontend. |
| Booking UX | Day view per server, 30-min cells, 7-day horizon. |
| Aliasing | Public & openly editable; no auth to view or change. |

## 2. Architecture

```
┌──────────── docker host (on LAN) ────────────┐
│                                              │
│  ┌─────────┐    ┌────────────────────────┐   │
│  │  Caddy  │───►│ FastAPI monitor        │   │
│  │ (HTTPS) │    │  ├─ /api/*  (agents)   │   │
│  │  :443   │    │  ├─ /web/* (browser)   │   │
│  └─────────┘    │  └─ /sse  (live feed)  │   │
│       ▲         └──────────┬─────────────┘   │
│       │                    │                 │
│       │              ┌─────▼─────┐           │
│       │              │  SQLite   │           │
│       │              │ (volume)  │           │
│       │              └───────────┘           │
└───────┼──────────────────────────────────────┘
        │  HTTPS (token auth) — agent uplink
        │
   ┌────┴───────────────────────────────────────┐
   │                                            │
┌──▼──────────────┐                  ┌──────────▼──────────┐
│ Windows Server  │                  │  Linux Server       │
│  agent.exe      │                  │  agent (systemd)    │
│  (Service)      │                  │                     │
│  qwinsta + WTS  │                  │  who, loginctl      │
└─────────────────┘                  └─────────────────────┘
```

Two long-running processes in the docker host (Caddy + FastAPI), one persistent volume (SQLite + Caddy state), and one small agent on each monitored server.

## 3. Components

### 3.1 Monitor (FastAPI)

Layout:

- `monitor/app/api/agents.py` — `POST /api/enroll`, `POST /api/report`. Token-authenticated.
- `monitor/app/api/web.py` — server-rendered Jinja templates for dashboard, server detail, aliases.
- `monitor/app/api/bookings.py` — booking CRUD endpoints (HTMX-friendly partial responses).
- `monitor/app/api/sse.py` — `GET /sse` long-lived stream that pushes JSON deltas to all subscribers.
- `monitor/app/core/sessions.py` — in-memory current-state cache, rebuilt from latest reports; SQLite is durable store.
- `monitor/app/core/bookings.py` — slot validation + conflict detection.
- `monitor/app/core/aliases.py` — read/write the device→alias map.
- `monitor/app/core/store.py` — thin SQLite layer.
- `monitor/templates/` — Jinja templates rendered with HTMX swaps.
- `monitor/static/` — Alpine.js + Pico.css (or similar small CSS), served from `/static`.

### 3.2 Agent (Python, packaged per OS)

Layout:

- `agent/server_monitor_agent/collect_windows.py` — uses `pywin32` (`win32ts.WTSEnumerateSessions`, `WTSQuerySessionInformation` for `WTSClientName`/`WTSConnectState`/`WTSUserName`/`WTSLogonTime`); falls back to parsing `quser`/`qwinsta` if APIs fail.
- `agent/server_monitor_agent/collect_linux.py` — parses `who`/`loginctl list-sessions` for SSH and console logins.
- `agent/server_monitor_agent/report.py` — main loop: collect every 5 s; if changed, POST a delta + heartbeat with bearer token; full snapshot every 60 s as resync.
- `agent/server_monitor_agent/service_windows.py` — `pywin32` service wrapper.
- `agent/server_monitor_agent/service_linux.py` — entrypoint for the systemd unit.
- `agent/installers/install.ps1`, `agent/installers/install.sh` — bootstrap scripts (download binary, register, start service).

The agent is packaged as a single executable per OS via PyInstaller, so the target server doesn't need a Python runtime. Same source tree, two release artifacts.

### 3.3 Frontend

- **Dashboard** (`/`) — grid of server cards. Each card shows hostname, OS icon, current sessions (alias if known, else raw device name + inline "Set alias…"), login time as "n minutes ago", idle indicator, agent-online dot. Live updates via SSE.
- **Server detail** (`/server/<id>?day=YYYY-MM-DD`) — same card data + 24-hour day timeline of 30-min cells, with bookings overlaid on actual session activity. Day picker for today + next 6 days. Click a free cell → modal: "Pick name (autocomplete from known members) → Confirm".
- **Members & aliases** (`/aliases`) — single table: `device name | alias | last seen`. Inline edit. The list of "known members" surfaced in booking autocomplete is the distinct set of non-empty alias values.

### 3.4 Reverse proxy (Caddy)

- Terminates HTTPS using a self-signed cert generated on first start (configurable to use a real cert if the team has internal PKI).
- Routes `/api/*` and `/sse` to FastAPI; `/`, `/static/*`, `/aliases`, `/server/*`, `/bookings/*` likewise.
- Stores its own state in a volume (`caddy_data/`) — gitignored.

## 4. Data model (SQLite)

```sql
-- Each monitored server.
CREATE TABLE servers (
  id                 INTEGER PRIMARY KEY,
  hostname           TEXT NOT NULL UNIQUE,
  os                 TEXT NOT NULL CHECK (os IN ('windows','linux')),
  enrollment_token   TEXT,                -- one-time, NULL after first successful enroll
  agent_token_hash   TEXT,                -- bcrypt hash of long-lived agent token
  first_seen_at      TEXT NOT NULL,
  last_seen_at       TEXT NOT NULL
);

-- Live + recent historical sessions.
CREATE TABLE sessions (
  id                 INTEGER PRIMARY KEY,
  server_id          INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  device_name        TEXT NOT NULL,       -- RDP source / SSH client hostname
  username           TEXT,                -- shared OS account name
  protocol           TEXT NOT NULL CHECK (protocol IN ('rdp','ssh','console')),
  state              TEXT NOT NULL CHECK (state IN ('active','disconnected')),
  logon_at           TEXT NOT NULL,
  last_seen_at       TEXT NOT NULL,
  ended_at           TEXT
);
CREATE INDEX idx_sessions_server_active ON sessions(server_id, ended_at);

-- Public, editable device -> human alias map. No auth to write.
CREATE TABLE aliases (
  device_name        TEXT PRIMARY KEY,
  alias              TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);

-- 30-minute slot reservations.
CREATE TABLE bookings (
  id                 INTEGER PRIMARY KEY,
  server_id          INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  start_at           TEXT NOT NULL,       -- ISO8601, must be on :00 or :30
  end_at             TEXT NOT NULL,       -- start_at + 30 min
  member_name        TEXT NOT NULL,       -- typed or picked
  note               TEXT,
  created_at         TEXT NOT NULL,
  UNIQUE(server_id, start_at)             -- prevents double-booking
);
CREATE INDEX idx_bookings_lookup ON bookings(server_id, start_at);

-- Raw audit trail of agent reports. Capped retention.
CREATE TABLE reports (
  id                 INTEGER PRIMARY KEY,
  server_id          INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  received_at        TEXT NOT NULL,
  payload_json       TEXT NOT NULL
);
CREATE INDEX idx_reports_received_at ON reports(received_at);
```

Notes:

- `aliases` is keyed by **device name** rather than by server. The same laptop maps to the same alias on every server it RDPs into.
- `sessions` keeps both live and recently-ended rows; the dashboard treats `ended_at IS NULL` as "currently logged in".
- A daily cleanup job prunes `sessions` with `ended_at < now-30d` and `reports.received_at < now-7d`.

## 5. Data flow

### 5.1 Agent enrollment (one-time per server)

1. An operator opens `/enroll` in the dashboard, types a hostname + selects OS, and clicks "Generate command".
2. The monitor inserts a `servers` row with a fresh `enrollment_token` (32 random bytes, base64-url, expires after 1 hour).
3. The page shows a single copy-paste install command (see § 6).
4. The operator runs the command on the target server (admin/root). The installer downloads the right agent binary, then `POST /api/enroll {hostname, os, enrollment_token}`.
5. Monitor verifies the enrollment token, generates a long-lived agent token (32 random bytes), stores its bcrypt hash on the `servers` row, and clears `enrollment_token`. Returns the plaintext token in the response (only opportunity to read it).
6. Installer writes the token to an OS-protected file (Windows: `%ProgramData%\server-monitor-agent\token`, restricted ACLs; Linux: `/etc/server-monitor-agent/token`, `chmod 0600`, `chown root:root`).
7. Service starts and begins reporting.

### 5.2 Steady-state reporting

- Every 5 seconds, the agent calls `collect()` and produces a snapshot of all current sessions on the host.
- It diffs against its last-sent snapshot. If anything changed, it `POST /api/report` with a JSON payload (sessions added/removed/changed + heartbeat). Authorization header: `Bearer <agent_token>`.
- Every 60 seconds (every 12th tick), the agent always sends a full snapshot for resync.
- The monitor: validates the bearer token by bcrypt-comparing against `agent_token_hash`; updates `sessions` (insert new, mark `ended_at` on missing, update `last_seen_at` on present); updates `servers.last_seen_at`; appends to `reports`; pushes a JSON delta to all SSE subscribers.

### 5.3 Browser updates

- `GET /` renders the dashboard from the current state of the `sessions`/`aliases`/`bookings`/`servers` tables.
- The page opens an SSE connection to `/sse`. The server pushes events as session/booking/alias changes occur.
- HTMX + Alpine swap the relevant DOM nodes in place; no full-page reload.

### 5.4 Booking flow

- Browser `GET /server/<id>?day=YYYY-MM-DD` renders a 48-cell grid for that day, overlaid with active/historical sessions.
- User clicks a free cell → modal asks for a name (typed, with autocomplete from distinct alias values) and an optional note.
- Submit → `POST /bookings {server_id, start_at, member_name, note?}`. Validation:
  - `start_at` ISO8601, second/microsecond zero, minute in {0, 30}.
  - `end_at = start_at + 30min`.
  - `start_at >= now` (no booking in the past).
  - `start_at <= now + 7 days`.
  - No existing booking on `(server_id, start_at)`.
- On success, returns the new row's HTMX partial; SSE broadcasts an event to all other browsers so their grids update without a manual refresh.
- Cancelling a booking is a `DELETE /bookings/<id>`; no auth, anyone can cancel anyone's (consistent with the trust model). The cancellation event is broadcast.

### 5.5 Stale-server detection

A background task runs every 30 s. Any `servers` row with `last_seen_at` older than 60 s is marked agent-offline; an SSE event is broadcast and the dashboard shows an "agent offline" badge. As soon as a fresh report arrives, it transitions back to online.

## 6. Onboarding a new server

This is a hard requirement: from a clean server to "showing on the dashboard" in under two minutes.

### 6.1 Windows (PowerShell, admin)

```powershell
iwr https://monitor.lan/install.ps1 -UseBasicParsing | iex; `
Install-MonitorAgent -Token <one-time-enrollment-token>
```

The script:

1. Downloads `agent.exe` from `https://monitor.lan/api/agent-binary?os=windows`.
2. Places it under `C:\Program Files\server-monitor-agent\`.
3. Registers a Windows Service (`sc.exe create` or `pywin32` service install).
4. Calls `POST /api/enroll`, receives the long-lived token, stores it under `%ProgramData%\server-monitor-agent\token` with restricted ACLs (SYSTEM + Administrators only).
5. Starts the service.

### 6.2 Linux (bash, root)

```bash
curl -fsSL https://monitor.lan/install.sh | sudo bash -s -- \
  --token <one-time-enrollment-token>
```

The script:

1. Downloads the right binary (`agent-linux-x86_64` or `agent-linux-aarch64`) from `/api/agent-binary?os=linux&arch=...`.
2. Installs it at `/usr/local/bin/server-monitor-agent`.
3. Installs a systemd unit at `/etc/systemd/system/server-monitor-agent.service`.
4. Calls `POST /api/enroll`, stores the long-lived token at `/etc/server-monitor-agent/token` (mode `0600`, owner `root:root`).
5. `systemctl enable --now server-monitor-agent`.

### 6.3 Re-enrollment / replacement

If a server is rebuilt, an admin clicks "Reset" on its row in `/enroll`. This regenerates an enrollment token and clears the old `agent_token_hash`. The new install command is run on the rebuilt host. The old `sessions`/`bookings` history is preserved (same `servers.id`).

## 7. Security & trust model

### 7.1 Trust boundaries

| Channel | Auth | Why |
| --- | --- | --- |
| Browser → monitor (web UI, bookings, aliases) | **None** | The team is trusted on the LAN; auth would add friction without a real threat model. |
| Agent → monitor (`/api/*`) | Bearer token (bcrypt hash stored, plaintext never logged) | An attacker who joins the LAN should not be able to push fake session reports for an existing server, even if they spoof the hostname. |
| Monitor TLS | Self-signed by Caddy on first start | Encrypts agent tokens in flight, also tidies up browser warnings on the LAN. Real PKI cert is configurable. |

### 7.2 What the system explicitly does **not** defend against

- A malicious team member who edits an alias or makes a fake booking. The honor-system framing is the entire premise; if that breaks, no software fix solves it.
- An attacker who already has root on a monitored server (they can read the agent token from disk).
- An attacker who already has shell on the docker host (they can read the SQLite DB).

### 7.3 Secret management

- **No secret is ever committed to the repo.** All secrets are generated at runtime and stored only in:
  - the SQLite DB (`agent_token_hash`, `enrollment_token`),
  - OS-protected files on agent hosts (the long-lived token),
  - Caddy's volume (TLS material).
- `.gitignore` excludes `.env`, `data/`, `*.sqlite*`, `*.pem`/`*.key`/`*.crt`, build artifacts, and editor/OS junk from commit one. A `.env.example` shows the env-var shape without real values.
- The monitor never logs token contents (only token IDs / prefixes if at all).

## 8. Error handling & edge cases

- **Agent loses network:** keeps last in-memory snapshot, reconnects with exponential backoff (max 60 s). On HTTP 401 (token revoked / unknown), the agent goes idle and surfaces an error in its local log; the operator must re-enroll.
- **Monitor restart:** agents reconnect after backoff; dashboard rehydrates from SQLite on first request. SSE clients reconnect automatically (browser-default behavior).
- **Multiple concurrent RDP sessions** (rare on Windows Server but possible with disconnected sessions): all are reported; dashboard lists them all, active first, then disconnected.
- **Disconnected vs active RDP:** distinct states. A disconnected session still occupies the single-user RDP slot until force-logoff, so it must be visible.
- **Clock skew between agent and monitor:** monitor stamps `received_at` itself; `logon_at` from the agent is shown as a relative time computed against the monitor clock to avoid future-time skew.
- **Race on booking the same slot:** `UNIQUE(server_id, start_at)` rejects the second insert with HTTP 409; the modal toasts "someone just took this slot" and refreshes the grid.
- **Empty alias:** dashboard falls back to raw device name + inline "Set alias…" link.
- **Duplicate hostname enrollment:** `servers.hostname` is `UNIQUE`; the second enroll attempt is rejected. Operator must use the "Reset" flow (§ 6.3).
- **Unicode in member names / aliases:** stored verbatim, HTML-escaped at render time.
- **Daylight saving:** all timestamps in DB are UTC; rendered in browser local time. Booking grid is built from UTC slots translated to local.

## 9. Testing approach

### 9.1 Unit tests (`pytest`)

- Booking validation: slot alignment, horizon, conflict detection, past-time rejection.
- Alias upsert and "known members" derivation.
- Agent snapshot diffing (given snapshot A and B, produce the right delta).
- Token verification (positive + negative cases) using a fixed bcrypt cost suitable for tests.

### 9.2 Integration tests

- Spin up FastAPI app + temp SQLite + a fake `collect()` that returns scripted snapshots.
- Drive the full flow: enroll → report → SSE event observed → booking → conflict → cancel.
- Use `httpx.AsyncClient` against the test app for endpoints; use an SSE client helper that yields events.

### 9.3 Manual smoke test (in README)

A short scripted procedure: spin up monitor, enroll one fake-Linux agent that emits a synthetic session, visit dashboard, confirm the alias inline edit works, confirm a booking renders. This catches regressions in the wiring that unit/integration tests miss (mostly template/HTMX issues).

## 10. Deployment

`docker-compose.yml` defines two services:

- `monitor` — image built from `monitor/Dockerfile`, mounts `./data` (SQLite) as a volume.
- `caddy` — official `caddy:latest`, mounts `Caddyfile` and `caddy_data/`/`caddy_config/` volumes; exposes 443 (and 80 for redirect) on the host.

`.env.example` documents:

- `MONITOR_HOST` (the public hostname agents will connect to, e.g. `monitor.lan`)
- `TIMEZONE` (display TZ; storage is always UTC)
- (no secrets — everything else is generated at runtime)

`docker compose up -d` starts both. First start auto-creates SQLite schema and Caddy's self-signed cert. To "restart fresh" the user just needs to `docker compose down` and remove `data/` and `caddy_data/`.

## 11. Open questions / future work (not blocking)

- Notification on session-start during a booked slot (Slack/email webhook) — out of v1.
- ARM Windows agent — out of v1; current target is x86_64 Windows Server.
- LDAP/SSO when the team grows beyond LAN trust — out of v1.
- Per-user booking views ("my bookings this week") — easy to add post-v1.
- Performance metrics (CPU/RAM/disk) — easy to layer on the same agent later.
