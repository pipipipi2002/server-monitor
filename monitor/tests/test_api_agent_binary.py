"""GET /api/agent-binary serves an OS-appropriate file from /agents-dist."""

from __future__ import annotations



async def test_serves_linux_binary_when_present(client, tmp_path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "agent-linux-x86_64").write_bytes(b"#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("AGENT_DIST_DIR", str(dist))

    r = await client.get("/api/agent-binary?os=linux&arch=x86_64")
    assert r.status_code == 200
    assert r.content == b"#!/bin/sh\nexit 0\n"


async def test_returns_404_when_binary_missing(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DIST_DIR", str(tmp_path / "empty"))
    r = await client.get("/api/agent-binary?os=linux&arch=x86_64")
    assert r.status_code == 404
