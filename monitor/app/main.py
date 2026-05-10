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
