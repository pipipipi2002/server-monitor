"""POST /api/enroll consumes a valid enrollment token and returns an agent token."""

from __future__ import annotations


async def _create_pending(client) -> tuple[str, str]:
    """Helper: create a pending server via the admin endpoint and return (hostname, enrollment_token)."""
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    assert r.status_code == 201, r.text
    return "srv-a", r.json()["enrollment_token"]


async def test_enroll_returns_agent_token(client) -> None:
    host, token = await _create_pending(client)
    r = await client.post(
        "/api/enroll",
        json={"hostname": host, "enrollment_token": token},
    )
    assert r.status_code == 200
    assert r.json()["agent_token"]


async def test_enroll_rejects_wrong_token(client) -> None:
    host, _ = await _create_pending(client)
    r = await client.post(
        "/api/enroll",
        json={"hostname": host, "enrollment_token": "bogus"},
    )
    assert r.status_code == 401


async def test_enroll_rejects_unknown_hostname(client) -> None:
    r = await client.post(
        "/api/enroll",
        json={"hostname": "ghost", "enrollment_token": "x"},
    )
    assert r.status_code == 401


async def test_enroll_is_one_shot(client) -> None:
    host, token = await _create_pending(client)
    ok = await client.post("/api/enroll", json={"hostname": host, "enrollment_token": token})
    assert ok.status_code == 200
    again = await client.post("/api/enroll", json={"hostname": host, "enrollment_token": token})
    assert again.status_code == 401
