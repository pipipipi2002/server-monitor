"""Web router serves the base layout and static assets."""

from __future__ import annotations


async def test_root_renders_html_with_title(client) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "<title>" in body
    assert "Server Monitor" in body


async def test_static_app_css_served(client) -> None:
    r = await client.get("/static/app.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]


async def test_root_includes_sse_subscriber_script(client) -> None:
    r = await client.get("/")
    assert "/sse" in r.text  # the SSE wiring partial is included
