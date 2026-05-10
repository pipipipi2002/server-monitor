"""GET /aliases (HTML page) and form POST /aliases."""

from __future__ import annotations


async def test_aliases_page_lists_existing(client) -> None:
    await client.post("/aliases", json={"device_name": "DESKTOP-A", "alias": "alice"})
    body = (await client.get("/aliases")).text
    assert "DESKTOP-A" in body
    assert "alice" in body
    assert "<form" in body  # there's an inline edit form


async def test_aliases_post_form_redirects_back(client) -> None:
    r = await client.post(
        "/aliases",
        data={"device_name": "DESKTOP-B", "alias": "bob"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    # form post returns the updated row partial (200) for HTMX, OR 303 for plain submit.
    assert r.status_code in (200, 303)
    body = (await client.get("/aliases")).text
    assert "DESKTOP-B" in body
    assert "bob" in body
