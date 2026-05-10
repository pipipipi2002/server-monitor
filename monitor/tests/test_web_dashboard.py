async def test_dashboard_lists_enrolled_server(client) -> None:
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    enroll = r.json()["enrollment_token"]
    await client.post("/api/enroll", json={"hostname": "srv-a", "enrollment_token": enroll})

    body = (await client.get("/")).text
    assert "srv-a" in body
    assert "linux" in body
    assert "agent offline" in body.lower() or "online" in body.lower()


async def test_dashboard_shows_session_with_alias(client) -> None:
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    enroll = r.json()["enrollment_token"]
    er = await client.post("/api/enroll", json={"hostname": "srv-a", "enrollment_token": enroll})
    token = er.json()["agent_token"]

    await client.post("/aliases", json={"device_name": "LAPTOP-A", "alias": "alice's laptop"})
    await client.post(
        "/api/report",
        json={
            "hostname": "srv-a",
            "received_at": "2030-01-01T12:00:00+00:00",
            "sessions": [{
                "device_name": "LAPTOP-A",
                "username": "shared",
                "protocol": "ssh",
                "state": "active",
                "logon_at": "2030-01-01T11:55:00+00:00",
            }],
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    body = (await client.get("/")).text
    # Jinja2 autoescape (which we keep on for XSS safety) renders an apostrophe as &#39;
    assert "alice&#39;s laptop" in body or "alice's laptop" in body
    assert "active" in body.lower()
