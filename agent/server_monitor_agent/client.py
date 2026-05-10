"""Tiny async HTTP client for the agent."""

from __future__ import annotations

import httpx


class ClientError(RuntimeError):
    pass


class AuthError(ClientError):
    pass


class Client:
    def __init__(
        self,
        *,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        verify: bool | str = True,
        timeout: float = 10.0,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            verify=verify,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def enroll(self, *, hostname: str, enrollment_token: str) -> str:
        r = await self._client.post(
            "/api/enroll",
            json={"hostname": hostname, "enrollment_token": enrollment_token},
        )
        if r.status_code == 401:
            raise AuthError(r.json().get("detail", "unauthorized"))
        r.raise_for_status()
        return r.json()["agent_token"]

    async def report(
        self,
        *,
        hostname: str,
        token: str,
        sessions: list[dict],
        received_at: str,
    ) -> None:
        r = await self._client.post(
            "/api/report",
            json={"hostname": hostname, "received_at": received_at, "sessions": sessions},
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code == 401:
            raise AuthError(r.json().get("detail", "unauthorized"))
        r.raise_for_status()
