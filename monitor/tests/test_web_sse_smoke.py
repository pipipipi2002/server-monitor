async def test_dashboard_html_subscribes_to_sse(client) -> None:
    body = (await client.get("/")).text
    assert "EventSource" in body or "sm:event" in body
    assert "id=\"server-grid\"" in body


async def test_event_includes_server_id_for_targeted_swap(client) -> None:
    """When the server publishes a 'report' event the SSE payload exposes hostname."""
    from app.api.sse import broadcaster

    q = broadcaster.subscribe()
    try:
        # Set up an enrolled server and post a report so the event fires.
        r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
        token = (await client.post(
            "/api/enroll",
            json={"hostname": "srv-a", "enrollment_token": r.json()["enrollment_token"]},
        )).json()["agent_token"]

        await client.post(
            "/api/report",
            json={
                "hostname": "srv-a",
                "received_at": "2030-01-01T12:00:00+00:00",
                "sessions": [],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        evt = q.get_nowait()
        assert evt["hostname"] == "srv-a"
    finally:
        broadcaster.unsubscribe(q)
