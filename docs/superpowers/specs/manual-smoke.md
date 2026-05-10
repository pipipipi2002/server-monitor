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
