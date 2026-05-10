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
