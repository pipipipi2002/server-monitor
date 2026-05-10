"""In-process broadcaster used by the /sse endpoint."""

from __future__ import annotations

import asyncio


async def test_subscriber_receives_published_event() -> None:
    from app.api.sse import Broadcaster

    b = Broadcaster()
    queue = b.subscribe()
    await b.publish({"type": "session.added", "device_name": "A"})
    msg = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert msg["type"] == "session.added"


async def test_late_subscriber_does_not_get_old_events() -> None:
    from app.api.sse import Broadcaster

    b = Broadcaster()
    await b.publish({"type": "x"})
    queue = b.subscribe()
    try:
        await asyncio.wait_for(queue.get(), timeout=0.05)
        assert False, "should not receive past events"
    except TimeoutError:
        pass


async def test_unsubscribe_releases_queue() -> None:
    from app.api.sse import Broadcaster

    b = Broadcaster()
    q = b.subscribe()
    b.unsubscribe(q)
    await b.publish({"type": "x"})
    assert q.qsize() == 0


async def test_publish_is_robust_to_full_subscriber_queue() -> None:
    from app.api.sse import Broadcaster

    b = Broadcaster(queue_max=1)
    q = b.subscribe()
    await b.publish({"n": 1})
    await b.publish({"n": 2})  # must not block forever; drop-oldest keeps the newest
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    # drop-oldest semantics: queue retains the most-recent event
    assert any(d["n"] == 2 for d in out)
    assert len(out) <= 1
