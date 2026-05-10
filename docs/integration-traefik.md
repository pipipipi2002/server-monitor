# Integrating server-monitor with an existing Traefik proxy

This is a recipe for the specific case of: *I already run Traefik on this host alongside Grafana/Prometheus on `*.services.local`, and I want to add `server-monitor` to that setup without disrupting anything.*

It assumes the deployment described in `DEVELOPMENT.md` §11 ("Coexisting with an existing reverse proxy"), specifically the **HTTP-browser + HTTPS-agent hybrid** — Option C / Option 3 from the discussion. The browser dashboard goes through your existing Traefik over plain HTTP (LAN-trusted); the agent uplink keeps TLS via Caddy's internal CA so the bearer tokens stay encrypted in transit.

If you're not in that situation, see `DEVELOPMENT.md` §11 for the broader options.

---

## Why this hybrid?

You said: *"I have no Let's Encrypt yet, my homelab is local and isolated, distributing a custom CA to many user devices is inconvenient, but the small number of monitored servers can have a CA installed."*

That set of constraints rules out:

- **Public CAs (Let's Encrypt etc.)** — they don't sign certs for `.local` TLDs, period.
- **Internal CA distributed to all team browsers** — too many devices, ongoing churn.
- **Plain HTTP for the agent uplink** — bearer tokens flowing over plaintext on the LAN. Not catastrophic given the trust model, but worse than free.

The hybrid threads the needle:

- Browsers see plain HTTP (your existing Traefik routes `monitor.services.local:80` → monitor container, just like Grafana). No certificate warnings, no CA distribution, identical UX to your other services.
- Agents — there are only a handful of them (one per monitored server) and they get touched once at install time anyway — connect over HTTPS with TLS verified against Caddy's internal CA, distributed automatically by the install script.
- The CA never touches a user's device.

The trust model is consistent with the rest of the design: trust the LAN for human-driven traffic, encrypt anything carrying long-lived credentials.

---

## Architecture

```
                 ┌─────────────────────────── HOST ────────────────────────────┐
                 │                                                              │
   Browser ──────┼──→ Traefik (:80) ─────→ proxy_net ─→ monitor:8000 (HTTP)     │
                 │     [docker provider]                       │                │
                 │                                             │                │
   Agent  ──────┼──→ Caddy   (:443, TLS) ─→ monitor_net ──────┘                │
                 │     [tls internal]                                            │
                 │                                                              │
                 │     monitor mounts caddy_data:ro so /ca.crt can serve        │
                 │     Caddy's root cert during agent enrollment.                │
                 └──────────────────────────────────────────────────────────────┘
```

Both routes terminate at the same `monitor` container. The container joins **two networks**:

- `proxy_net` (yours; shared with Traefik, Grafana, Prometheus) — Traefik discovers monitor here via its docker labels.
- `monitor_net` (new; internal to the server-monitor stack) — Caddy reaches monitor here.

Caddy doesn't need to be on `proxy_net`. The two reverse proxies live on different networks and share only the host IP and port assignments (Traefik on 80, Caddy on 443).

---

## Files this stack adds

| File | Purpose |
|---|---|
| `docker-compose.traefik.yml` | Compose definition: monitor + Caddy, integrated with `proxy_net`. |
| `Caddyfile.traefik` | Caddy config for the agent uplink (no port-80 block; Traefik owns 80). |

The default `docker-compose.yml` and `Caddyfile` aren't deleted — you'd just stop using them in this deployment. Keeping them in the repo lets a fresh-host setup still work via the original quick-start.

---

## Prerequisites

1. **An existing Traefik instance** running on `proxy_net` (or whatever your network is called). Its `web` entrypoint must be on host port 80. If your network has a different name, edit `docker-compose.traefik.yml`:
   ```yaml
   networks:
     proxy_net:
       external: true
       name: <your-network-name>   # e.g. proxy_net or traefik_default
   ```

2. **DNS** for `monitor.services.local` (or whatever you set `MONITOR_HOST` to) resolving to this host's IP — on **every device** that touches it:
   - Team members' laptops (so the dashboard loads).
   - Each monitored server (so the agent can reach the monitor).
   - Most homelabs do this with a Pi-hole / dnsmasq / Unbound override, or `/etc/hosts` entries.

3. **Built agent binaries** in `./agents-dist/`. Run `./scripts/build_agents.sh` once on a Linux host with Python 3.12. Windows .exe must be built separately on a Windows host (see README §3).

4. **Host port 443** must be free. Your existing Traefik takes 80 and 8080; we add Caddy on 443. If something else is listening on 443, free it first:
   ```bash
   ss -ltnp | grep ':443 '
   ```

---

## Migration steps

If you're starting fresh, jump to step 4. The earlier steps explain what changes from the default README quick-start.

### 1 — Decide your hostname

Convention from your existing stack: `<service>.services.local`. So the natural choice is `monitor.services.local`. Add a DNS record (or `/etc/hosts` entry on each device) pointing it at the host IP.

### 2 — Set environment

```bash
cp .env.example .env
$EDITOR .env
```

Set `MONITOR_HOST=monitor.services.local`. Other defaults are fine.

### 3 — Build agent binaries

```bash
./scripts/build_agents.sh
ls -lh agents-dist/                 # verify agent-linux-x86_64 exists
```

(If you have Windows servers to monitor, build `agent-windows.exe` separately on a Windows host and copy it into `agents-dist/`.)

### 4 — Bring it up with the Traefik-aware compose file

```bash
docker compose -f docker-compose.traefik.yml up -d
docker compose -f docker-compose.traefik.yml logs -f
```

Two services start:

- `server-monitor` — joins `proxy_net` and `monitor_net`. Traefik's docker provider sees the labels and starts routing immediately.
- `server-monitor-caddy` — binds host:443. On first start it generates an internal CA and a leaf cert for `monitor.services.local`; you'll see `provisioning ca` then `successfully cleaned up storage units` in the logs.

### 5 — Verify both ingress paths

```bash
# Browser path through Traefik on plain HTTP
curl -fsS http://monitor.services.local/ | head -1
# → <!doctype html ...>

# Agent path through Caddy on HTTPS, with TLS verification skipped (we don't have the CA yet)
curl -kfsS https://monitor.services.local/ca.crt | head -1
# → -----BEGIN CERTIFICATE-----
```

If the first call returns the dashboard HTML and the second returns a CERTIFICATE block, you're correctly wired.

### 6 — Enroll your first server

Open `http://monitor.services.local/enroll` in a browser, generate a token. The page renders an install command that **defaults to `https://...`** for the install script fetch — that's fine *if* the operator running the command already trusts the Caddy cert (e.g. they previously clicked through a browser warning, or they have the Caddy CA pre-installed).

For the typical case (operator has none of that), edit the `curl` URL on first use to use HTTP through Traefik:

**Linux**

```bash
# What the page generates:
curl -fsSL https://monitor.services.local/install.sh | sudo bash -s -- \
    --token <T> --hostname <H>

# What you actually run (note the http://):
curl -fsSL http://monitor.services.local/install.sh | sudo bash -s -- \
    --token <T> --hostname <H> \
    --monitor-url https://monitor.services.local
```

**Windows (PowerShell-as-admin)**

```powershell
# What the page generates:
iwr https://monitor.services.local/install.ps1 -UseBasicParsing | iex
Install-MonitorAgent -Token <T> -Hostname <H>

# What you actually run:
iwr http://monitor.services.local/install.ps1 -UseBasicParsing | iex
Install-MonitorAgent -Token <T> -Hostname <H> -MonitorUrl https://monitor.services.local
```

The two changes:

- `https://` → `http://` for the script fetch — goes through Traefik, no TLS, no warning.
- Explicit `--monitor-url https://monitor.services.local` so the agent reports over HTTPS through Caddy.

The script itself does the right thing internally:

1. `curl -kfsSL https://monitor.services.local/ca.crt` → Caddy → returns CA cert. Pinned to `/etc/server-monitor-agent/ca.pem`.
2. `curl -fsSL --cacert /etc/server-monitor-agent/ca.pem https://monitor.services.local/api/agent-binary?...` → Caddy → returns the binary. **Verified.**
3. `server-monitor-agent enroll --enrollment-token <T> --ca-bundle /etc/server-monitor-agent/ca.pem --monitor-url https://monitor.services.local` → Caddy → exchanges enrollment token for an agent token. **Verified.**
4. systemd unit installed, service started. Every report from now on uses TLS verified against the pinned CA.

The single insecure call is step 1 (CA bootstrap) — trust-on-first-use, accepted explicitly under the LAN-trust model.

### 7 — Verify the agent is reporting

```bash
# On the monitored server
sudo journalctl -u server-monitor-agent -f

# In your browser
http://monitor.services.local/
# server card should show "agent online" within ~10s
```

---

## Operating notes

### Where each service lives

| | Container | Network | Host port |
|---|---|---|---|
| Browser dashboard | `monitor` (FastAPI) | `proxy_net` | 80 (via your Traefik) |
| Agent uplink | `monitor` (FastAPI) | `monitor_net` | 443 (via this stack's Caddy) |
| Caddy admin / metrics | `caddy` | `monitor_net` | n/a (not exposed) |
| Caddy CA storage | volume `caddy_data` | n/a | n/a |

### Updating the install command in `enroll.html`

If the manual `https://` → `http://` substitution becomes annoying, edit the template to render HTTP by default. Two-line change in `monitor/app/templates/enroll.html`:

```diff
-curl -fsSL https://{{ monitor_host }}/install.sh | sudo bash -s -- \
+curl -fsSL http://{{ monitor_host }}/install.sh | sudo bash -s -- \
   --token {{ command.token }} \
-  --hostname {{ command.hostname }}
+  --hostname {{ command.hostname }} \
+  --monitor-url https://{{ monitor_host }}
```

Same idea for the PowerShell branch (`iwr http://...` and add `-MonitorUrl https://...`).

This is a deployment-specific tweak — keep it on a local branch or feature-flag it via env if you want to keep the upstream layout.

### Renewing the Caddy CA

Caddy auto-renews leaf certs. The internal CA root is good for ~10 years and renews itself before expiry. If you ever wipe `caddy_data/`, every agent loses trust until re-enrolled — there's no automatic re-trust path. So **don't**.

If you ever need to rotate the CA deliberately, the procedure is:

1. Stop everything: `docker compose -f docker-compose.traefik.yml down`.
2. Delete `caddy_data` volume: `docker volume rm <stack>_caddy_data`.
3. Bring it up: a new CA is minted on first start.
4. From the dashboard, click **Reset** on every server row. This generates fresh enrollment tokens.
5. Re-run the install command on each monitored server.

### Coexistence sanity checks

- Make sure your Traefik `command:` block doesn't add `--entrypoints.websecure.address=:443`. If it tries to bind 443, it conflicts with this stack's Caddy. Your config already doesn't, but it's worth checking after any Traefik upgrade.
- The labels on the monitor container use `entrypoints=web` (HTTP), matching how Grafana/Prometheus are configured. If you later add a `websecure` entrypoint to Traefik with TLS, do **not** point monitor's labels there — that would put the dashboard behind TLS too, and you've already opted into the HTTP-browser model. Stick with `entrypoints=web`.

### What happens on a Traefik restart

Traefik watches the docker socket. When you restart Traefik, it discovers `server-monitor` via labels and starts routing within seconds. No action needed in this stack.

When you restart this stack, Caddy comes back with the same CA (persisted in `caddy_data`), so all enrolled agents continue working without re-enrollment.

---

## Trust model summary (for this hybrid)

| Channel | Auth | Confidentiality |
|---|---|---|
| Browser → Traefik → monitor (dashboard, aliases, bookings) | None — open on the LAN | None (HTTP). LAN-trusted. |
| Agent → Caddy → monitor (`/api/enroll`) | Bearer one-shot enrollment token | TLS (Caddy internal CA) |
| Agent → Caddy → monitor (`/api/report`) | Bearer agent token (long-lived, hashed at rest) | TLS (Caddy internal CA) |
| Operator → monitor (CA bootstrap, `/ca.crt` over plain HTTP via Traefik) | None | None — single TOFU connection |

The bearer-token-bearing channels stay encrypted; the human-driven channels stay simple. No CA distribution required for human-side devices.

---

## When to revisit this

- If you eventually buy a real domain and put the homelab behind Let's Encrypt (DNS-01), come back to this doc and switch to the "external Traefik with real certs" path in `DEVELOPMENT.md` §11. The agent install scripts can drop the CA dance entirely once the cert is publicly trusted.
- If the team grows and you want SSO + audit trails, the design in `docs/superpowers/specs/2026-05-10-server-monitor-design.md` §1.2 explicitly lists per-user auth as out-of-scope. That's the right next-spec to write before adding it.
- If you want to expose the dashboard to the internet, do not just open port 80 — put it behind a real reverse proxy with TLS, an authentication layer (Authelia / OAuth proxy / similar), and rate limiting. The "trust the LAN" model in this design is doing a lot of work.
