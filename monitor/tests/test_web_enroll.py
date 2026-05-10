async def test_enroll_page_lists_servers(client) -> None:
    await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    body = (await client.get("/enroll")).text
    assert "srv-a" in body
    assert "<form" in body  # creation form present


async def test_enroll_page_form_creates_pending_server_and_shows_command(client) -> None:
    r = await client.post(
        "/enroll",
        data={"hostname": "srv-b", "os": "linux"},
    )
    assert r.status_code == 200
    body = r.text
    assert "srv-b" in body
    assert "install.sh" in body  # the install command is displayed
