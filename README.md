# server-monitor

A self-hosted dashboard for monitoring RDP and SSH session activity across a small fleet of Windows and Linux servers, with optional 30-minute slot reservations to coordinate access.

Solves the "kicked out of RDP" problem — when one teammate connects to a Windows server they boot whoever was on it. Now the dashboard tells you at a glance who's currently in, since when, and which device they're remoting from.

## Goals

- Show who is currently logged in on each server (RDP source device on Windows, SSH client on Linux), including login time and session state.
- Map raw device names (e.g. `DESKTOP-AB12C`) to human-friendly aliases that the whole team can see and edit.
- Let team members reserve 30-minute slots on a server (informational, honor system) so others know to stay off.
- Keep onboarding a new server to a single install command (< 2 minutes from clean machine to dashboard entry).
- Single-host Docker deployment via `docker compose up`.

## Architecture

A FastAPI application (with a SQLite store and a Caddy reverse proxy) runs in Docker on the internal network. A small Python agent runs as a service on each monitored server (Windows Service or systemd unit), polls local session APIs every ~5 seconds, and pushes deltas to the monitor over HTTPS authenticated by a per-server token. Browsers receive live updates via Server-Sent Events.

Full design: [`docs/superpowers/specs/2026-05-10-server-monitor-design.md`](docs/superpowers/specs/2026-05-10-server-monitor-design.md).

## Trust model

The team is fully trusted on the LAN. The web UI has no logins — anyone on the network can view the dashboard, edit aliases, and create bookings (honor system). The only authenticated channel is **agent → monitor**, so foreign hosts can't push fake session reports.

---

## Setup

### Prerequisites

| | |
|---|---|
| **Monitor host** | Linux or macOS with Docker Engine + `docker compose` plugin. ~200 MB RAM is plenty. |
| **Agent build host (Linux)** | Python 3.12+, plus `python3.12-venv` package on Debian/Ubuntu (`sudo apt install python3.12-venv`). Only needed once, to bake the binary. |
| **Agent build host (Windows)** | Required if you have any Windows servers to monitor. Python 3.12+, ability to run `pyinstaller`. The Windows .exe **must** be built on a Windows host — cross-compiling from Linux isn't supported. |
| **Each monitored server** | root/admin shell (one-time, for the install command). Network access from the server to the monitor on TCP/443 (and TCP/80 once for the initial CA fetch). |

### 1 — Configure

```bash
cp .env.example .env
$EDITOR .env
```

The defaults are safe; the only knob you'll commonly change is `MONITOR_HOST`. The monitor listens on whatever hostname agents will dial; if your team uses `monitor.lan` over an internal DNS or `/etc/hosts` entry, leave it as-is.

```ini
# Public hostname agents will dial.
MONITOR_HOST=monitor.lan

# Display timezone for the dashboard. Storage is always UTC.
DISPLAY_TZ=UTC

# Bcrypt cost for hashing agent tokens. 10 ≈ 50 ms per verify; tests use 4.
BCRYPT_COST=10

# Lifetime of an enrollment token (the one-shot value baked into install commands).
ENROLLMENT_TOKEN_TTL=3600
```

### 2 — Build the Linux agent binary

```bash
./scripts/build_agents.sh
```

Produces `agents-dist/agent-linux-x86_64` (~12 MB, single-file, no Python runtime needed on target). Re-run any time the agent code changes.

### 3 — Build the Windows agent binary (only if you have Windows servers)

On a Windows host with Python 3.12 and an admin PowerShell:

```powershell
git clone <this-repo>
cd server-monitor
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -e .\agent[dev]
.\.venv\Scripts\pyinstaller --clean --distpath .\agents-dist agent\installers\pyinstaller_windows.spec
```

Copy `agents-dist\agent-windows.exe` back to the monitor host's `agents-dist/` directory before bringing the stack up.

### 4 — Bring it up

```bash
docker compose up -d
docker compose logs -f
```

First start auto-creates the SQLite schema in `./data/` and Caddy's internal CA in the `caddy_data` volume. Browse to `https://${MONITOR_HOST}/` — you'll see a TLS warning the first time (self-signed cert from Caddy's local CA); accept it.

### 5 — Onboard the first server

Open `https://${MONITOR_HOST}/enroll`, type the server's hostname, choose its OS, and click **Generate install command**. Copy-paste the displayed command onto the target server's admin shell:

**Linux** (run as root):

```bash
curl -fsSL https://${MONITOR_HOST}/install.sh | sudo bash -s -- \
    --token <generated-token> \
    --hostname <server-hostname>
```

**Windows** (run from an elevated PowerShell):

```powershell
iwr https://${MONITOR_HOST}/install.ps1 -UseBasicParsing | iex
Install-MonitorAgent -Token <generated-token> -Hostname <server-hostname>
```

Within ~10 seconds the server appears on the dashboard with a green **agent online** badge.

---

## Usage

### Dashboard (`/`)

Card per server. Each card shows:

- Hostname, OS, agent online/offline badge.
- Active sessions: alias (or raw device name if no alias is set), session state (`active` / `disconnected`), login time as "X minutes ago".
- Click the hostname for the per-server detail view.

Live updates arrive via SSE — when an agent reports a new session, every browser sees the change in a few seconds with no refresh.

### Aliases (`/aliases`)

The map from RDP/SSH source device names to human aliases. Open to anyone on the LAN — view, add, or change. There are no logins; we trust the team.

- **Add:** type a device name (e.g. `DESKTOP-AB12C`) and an alias (e.g. `alice's laptop`), submit.
- **Edit:** click the alias field on any row, change it, hit Save. Updates immediately for everyone.

The set of distinct alias values is the autocomplete pool when booking slots.

### Server detail & booking (`/server/<id>`)

For each server, a 24-hour day grid of 30-minute slots. Day picker lets you flip through today + the next 6 days. Click an empty cell, pick or type your name, optionally add a note, confirm. The cell flips to "booked" for everyone immediately.

- Bookings are **informational, honor system.** The agent does nothing about them — they exist so teammates know to stay off.
- A double-booking returns 409 in the network tab; the dashboard refreshes the grid so you see the existing booking instead of overwriting.
- Anyone on the LAN can cancel any booking (consistent with the trust model).

### Adding more servers

Repeat step 5 above for each additional server. Each gets a fresh enrollment token; the install command is one-shot and expires after `ENROLLMENT_TOKEN_TTL` seconds (default 1 hour).

---

## Operations

### Re-enrolling a rebuilt server

If you reinstall a Windows VM or replace a host with the same hostname, the old agent token won't work on the new box. From `/enroll`, find the row for that server and click **Reset** — that clears the old token and shows a fresh install command. Run it on the new host. Historical session/booking rows for the old machine are preserved (same `servers.id`).

### Removing a server

Delete the row directly from the SQLite DB on the monitor host:

```bash
docker compose exec monitor sqlite3 /data/server-monitor.sqlite \
    "DELETE FROM servers WHERE hostname='retired-host';"
```

The cascading FK on sessions/bookings/reports tidies the rest. (A delete button on the UI is on the wishlist; it's deliberately not auto-exposed because servers shouldn't disappear from history just because a checkbox got clicked.)

### Stopping an agent on a server

**Linux:** `sudo systemctl stop server-monitor-agent && sudo systemctl disable server-monitor-agent`
**Windows:** `Stop-Service ServerMonitorAgent; sc.exe delete ServerMonitorAgent` (admin PowerShell).

The dashboard will mark the server "agent offline" within ~60 s.

### Viewing logs

```bash
# Monitor + Caddy
docker compose logs -f monitor caddy

# Linux agent
sudo journalctl -u server-monitor-agent -f

# Windows agent (admin PowerShell)
Get-EventLog -LogName Application -Source ServerMonitorAgent -Newest 50
```

### Backing up

The whole monitor state is in `./data/server-monitor.sqlite` and the Caddy volume. The DB is the only thing worth backing up — TLS material regenerates on first start if you nuke `caddy_data`.

```bash
docker compose exec monitor sqlite3 /data/server-monitor.sqlite ".backup /data/backup.sqlite"
docker cp $(docker compose ps -q monitor):/data/backup.sqlite ./backups/$(date +%F).sqlite
```

### Restarting fresh

```bash
docker compose down
rm -rf data/ caddy_data/   # ⚠ wipes all history + TLS state
docker compose up -d
```

You'll need to re-enroll every server.

---

## Configuration reference

| Env var | Default | Description |
|---|---|---|
| `MONITOR_HOST` | `monitor.lan` | Hostname agents dial. Used in install commands and Caddy's TLS SNI. |
| `DISPLAY_TZ` | `UTC` | Timezone shown in the UI. Storage is always UTC. |
| `BCRYPT_COST` | `10` | Bcrypt rounds for hashing agent tokens. |
| `ENROLLMENT_TOKEN_TTL` | `3600` | Seconds before a one-shot enrollment token expires. |
| `DB_PATH` | `/data/server-monitor.sqlite` | SQLite path **inside the container.** Don't change unless you also move the volume mount. |
| `AGENT_DIST_DIR` | `/agents-dist` | Where the monitor looks for built binaries to serve via `/api/agent-binary`. |
| `CADDY_CA_PATH` | `/caddy/data/caddy/pki/authorities/local/root.crt` | Path to the Caddy internal CA cert that the monitor exposes at `/ca.crt`. |

The agent's own configuration is via CLI flags, set in the systemd unit (Linux) or service `binPath` (Windows): `--monitor-url`, `--hostname`, `--token-file`, `--ca-bundle`, `--interval`. The install scripts wire all of these for you.

---

## Troubleshooting

| Symptom | Most likely cause |
|---|---|
| Browser warns about TLS on first visit | Caddy uses an internal CA; this is expected on the LAN. Accept the cert once. |
| Server shows "agent offline" right after install | Check `journalctl -u server-monitor-agent` (Linux) or the Windows event log. Usually a wrong `MONITOR_URL` or DNS not resolving the hostname. |
| `/api/enroll` returns 401 on a fresh install | The enrollment token has expired (default 1 hour). Hit Reset on the enroll page and run the new install command. |
| `docker compose up` fails on Caddy with "auto_https" errors | The monitor host can't bind 80/443. Check whether something else is listening: `ss -ltnp \| grep -E ':(80\|443) '`. |
| Linux agent build fails: `ensurepip is not available` | Install the venv package: `sudo apt install python3.12-venv` (Debian/Ubuntu). |
| Windows install script can't import the CA | The script trusts the cert via `Import-Certificate`. Make sure you ran from an elevated PowerShell — non-admin can't write to `Cert:\LocalMachine\Root`. |
| Booking returns 409 | Someone else booked the same slot a moment before you. Refresh and pick another cell. |

---

## Development

The repo uses standard Python tooling. To set up a dev environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -e 'monitor[dev]' -e 'agent[dev]'
.venv/bin/pytest                              # 108 tests, ~3s
.venv/bin/ruff check monitor agent scripts    # lint
.venv/bin/uvicorn app.main:app --reload --app-dir monitor   # run the monitor without docker
```

Architecture and design rationale: [`docs/superpowers/specs/2026-05-10-server-monitor-design.md`](docs/superpowers/specs/2026-05-10-server-monitor-design.md).
Implementation history (every step that produced this codebase): [`docs/superpowers/plans/2026-05-10-server-monitor-implementation.md`](docs/superpowers/plans/2026-05-10-server-monitor-implementation.md).
Manual smoke test: [`docs/superpowers/specs/manual-smoke.md`](docs/superpowers/specs/manual-smoke.md).

---

## Repo safety

**No secrets are stored in this repository.** All runtime secrets — agent enrollment tokens, long-lived agent auth tokens, and TLS material — are generated at runtime and live only in:

- the SQLite database (which is in `data/`, ignored by git), and
- OS-protected files on agent hosts (`%ProgramData%\server-monitor-agent\token` on Windows; `/etc/server-monitor-agent/token`, mode `0600`, on Linux).

`.gitignore` excludes `.env`, `data/`, `*.sqlite*`, `*.pem`/`*.key`/`*.crt`, build artifacts, and editor/OS junk from commit one onward, so an accidental `git add -A` can't leak runtime state.

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

## License

TBD.
