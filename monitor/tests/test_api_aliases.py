"""POST /aliases, GET /api/aliases, GET /api/aliases/members."""

from __future__ import annotations


async def test_upsert_alias_then_list(client) -> None:
    r = await client.post(
        "/aliases", json={"device_name": "DESKTOP-AB12C", "alias": "alice"}
    )
    assert r.status_code == 200
    rl = await client.get("/api/aliases")
    items = rl.json()["items"]
    assert any(i["device_name"] == "DESKTOP-AB12C" and i["alias"] == "alice" for i in items)


async def test_known_members_list(client) -> None:
    await client.post("/aliases", json={"device_name": "A", "alias": "alice"})
    await client.post("/aliases", json={"device_name": "B", "alias": "bob"})
    r = await client.get("/api/aliases/members")
    assert r.json()["members"] == ["alice", "bob"]


async def test_empty_alias_returns_422(client) -> None:
    r = await client.post("/aliases", json={"device_name": "A", "alias": "   "})
    assert r.status_code == 422


async def test_alias_change_publishes_sse(client) -> None:
    from app.api.sse import broadcaster

    q = broadcaster.subscribe()
    try:
        await client.post("/aliases", json={"device_name": "A", "alias": "alice"})
        evt = q.get_nowait()
        assert evt["type"] == "alias.updated"
        assert evt["device_name"] == "A"
        assert evt["alias"] == "alice"
    finally:
        broadcaster.unsubscribe(q)
