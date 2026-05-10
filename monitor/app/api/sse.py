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
