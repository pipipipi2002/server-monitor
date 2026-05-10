"""POST /bookings, DELETE /bookings/<id>, listing per server+day."""

from __future__ import annotations


async def _server(client) -> int:
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    return r.json()["server_id"]


async def test_create_booking_returns_201_with_id(client) -> None:
    sid = await _server(client)
    r = await client.post(
        "/bookings",
        json={
            "server_id": sid,
            "start_at": "2030-01-01T14:00:00+00:00",
            "member_name": "alice",
        },
    )
    assert r.status_code == 201
    assert r.json()["id"]


async def test_duplicate_booking_returns_409(client) -> None:
    sid = await _server(client)
    payload = {"server_id": sid, "start_at": "2030-01-01T14:00:00+00:00", "member_name": "alice"}
    await client.post("/bookings", json=payload)
    again = await client.post("/bookings", json={**payload, "member_name": "bob"})
    assert again.status_code == 409


async def test_invalid_slot_returns_422(client) -> None:
    sid = await _server(client)
    r = await client.post(
        "/bookings",
        json={"server_id": sid, "start_at": "2030-01-01T14:15:00+00:00", "member_name": "alice"},
    )
    assert r.status_code == 422


async def test_list_bookings_for_day(client) -> None:
    sid = await _server(client)
    await client.post(
        "/bookings",
        json={"server_id": sid, "start_at": "2030-01-01T14:00:00+00:00", "member_name": "alice"},
    )
    await client.post(
        "/bookings",
        json={"server_id": sid, "start_at": "2030-01-02T14:00:00+00:00", "member_name": "bob"},
    )
    r = await client.get(f"/bookings?server_id={sid}&day=2030-01-01")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["member_name"] == "alice"


async def test_delete_booking(client) -> None:
    sid = await _server(client)
    r = await client.post(
        "/bookings",
        json={"server_id": sid, "start_at": "2030-01-01T14:00:00+00:00", "member_name": "alice"},
    )
    bid = r.json()["id"]
    rd = await client.delete(f"/bookings/{bid}")
    assert rd.status_code == 204
    rd2 = await client.delete(f"/bookings/{bid}")
    assert rd2.status_code == 404
