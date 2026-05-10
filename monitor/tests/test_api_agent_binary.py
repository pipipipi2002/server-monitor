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


async def test_serves_agent_helper_when_present(client, tmp_path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "nssm.exe").write_bytes(b"MZ\x00\x00fake-pe-binary")
    monkeypatch.setenv("AGENT_DIST_DIR", str(dist))

    r = await client.get("/api/agent-helper/nssm.exe")
    assert r.status_code == 200
    assert r.content.startswith(b"MZ")


async def test_agent_helper_404_when_missing(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DIST_DIR", str(tmp_path / "empty"))
    r = await client.get("/api/agent-helper/nssm.exe")
    assert r.status_code == 404


async def test_agent_helper_rejects_unsafe_names(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DIST_DIR", str(tmp_path))
    # FastAPI's path-param routing strips path-traversal sequences and rejects
    # `/` in single-segment params, so we test the regex-level reject path with
    # a name containing a character outside the [A-Za-z0-9._-] set.
    r = await client.get("/api/agent-helper/has%20space")
    assert r.status_code == 400
