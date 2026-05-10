"""HTTP client uses bearer auth and the right URL paths."""

from __future__ import annotations

import json

import httpx
import pytest


def _mock_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_enroll_returns_agent_token() -> None:
    from server_monitor_agent.client import Client

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/enroll"
        body = json.loads(request.content)
        assert body == {"hostname": "h", "enrollment_token": "tok"}
        return httpx.Response(200, json={"agent_token": "agent-tok"})

    c = Client(base_url="https://m.lan", transport=_mock_transport(handler), verify=False)
    assert await c.enroll(hostname="h", enrollment_token="tok") == "agent-tok"


@pytest.mark.asyncio
async def test_report_uses_bearer_header() -> None:
    from server_monitor_agent.client import Client

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer agent-tok"
        body = json.loads(request.content)
        assert body["hostname"] == "h"
        assert body["sessions"] == [{"x": 1}]
        return httpx.Response(200, json={"accepted": True})

    c = Client(base_url="https://m.lan", transport=_mock_transport(handler), verify=False)
    await c.report(
        hostname="h",
        token="agent-tok",
        sessions=[{"x": 1}],
        received_at="2030-01-01T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_report_raises_on_401() -> None:
    from server_monitor_agent.client import AuthError, Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "nope"})

    c = Client(base_url="https://m.lan", transport=_mock_transport(handler), verify=False)
    with pytest.raises(AuthError):
        await c.report(
            hostname="h", token="bad", sessions=[],
            received_at="2030-01-01T00:00:00+00:00",
        )
