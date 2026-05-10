"""Loop sends a snapshot when state changes; resyncs every Nth tick."""

from __future__ import annotations

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
