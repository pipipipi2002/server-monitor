async def test_app_responds_on_root(client) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text
