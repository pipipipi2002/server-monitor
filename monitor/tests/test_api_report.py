"""POST /api/report ingests an agent snapshot."""

from __future__ import annotations


async def _enroll(client) -> tuple[str, str]:
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    enroll_tok = r.json()["enrollment_token"]
    r = await client.post(
        "/api/enroll", json={"hostname": "srv-a", "enrollment_token": enroll_tok}
    )
    return "srv-a", r.json()["agent_token"]


async def test_report_accepts_valid_snapshot(client) -> None:
    host, token = await _enroll(client)
    r = await client.post(
        "/api/report",
        json={
            "hostname": host,
            "received_at": "2030-01-01T12:00:00+00:00",
            "sessions": [
                {
                    "device_name": "LAPTOP-A",
                    "username": "shared",
                    "protocol": "ssh",
                    "state": "active",
                    "logon_at": "2030-01-01T11:55:00+00:00",
                }
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True


async def test_report_rejects_missing_token(client) -> None:
    host, _ = await _enroll(client)
    r = await client.post(
        "/api/report",
        json={"hostname": host, "received_at": "x", "sessions": []},
    )
    assert r.status_code == 401


async def test_report_rejects_wrong_token(client) -> None:
    host, _ = await _enroll(client)
    r = await client.post(
        "/api/report",
        json={"hostname": host, "received_at": "2030-01-01T12:00:00+00:00", "sessions": []},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


async def test_report_publishes_sse_event(client) -> None:
    """When a report changes session state, an SSE event is published."""
    host, token = await _enroll(client)

    from app.api.sse import broadcaster

    queue = broadcaster.subscribe()
    try:
        r = await client.post(
            "/api/report",
            json={
                "hostname": host,
                "received_at": "2030-01-01T12:00:00+00:00",
                "sessions": [
                    {
                        "device_name": "LAPTOP-A",
                        "username": "shared",
                        "protocol": "rdp",
                        "state": "active",
                        "logon_at": "2030-01-01T11:55:00+00:00",
                    }
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        evt = queue.get_nowait()
        assert evt["type"] == "report"
        assert evt["hostname"] == host
    finally:
        broadcaster.unsubscribe(queue)
