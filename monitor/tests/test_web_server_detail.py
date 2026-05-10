async def test_server_detail_renders_day_grid(client) -> None:
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    sid = r.json()["server_id"]
    body = (await client.get(f"/server/{sid}?day=2030-01-01")).text
    # 48 half-hour cells
    assert body.count("sm-slot") >= 48
    assert "00:00" in body and "23:30" in body


async def test_server_detail_marks_booked_cell(client) -> None:
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    sid = r.json()["server_id"]
    await client.post(
        "/bookings",
        json={"server_id": sid, "start_at": "2030-01-01T14:00:00+00:00", "member_name": "alice"},
    )
    body = (await client.get(f"/server/{sid}?day=2030-01-01")).text
    assert "sm-slot--booked" in body
    assert "alice" in body


async def test_server_detail_unknown_id_returns_404(client) -> None:
    r = await client.get("/server/999?day=2030-01-01")
    assert r.status_code == 404
