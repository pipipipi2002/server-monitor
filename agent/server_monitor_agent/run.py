"""Main agent loop: collect → diff → report."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
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
