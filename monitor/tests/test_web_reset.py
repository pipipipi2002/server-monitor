async def test_reset_button_regenerates_enrollment_token(client) -> None:
    r = await client.post("/api/admin/server", json={"hostname": "srv-a", "os": "linux"})
    sid = r.json()["server_id"]
    enroll = r.json()["enrollment_token"]
    # complete enrollment so we have a real agent_token_hash to clear
    await client.post("/api/enroll", json={"hostname": "srv-a", "enrollment_token": enroll})

    r = await client.post(f"/enroll/{sid}/reset")
    assert r.status_code == 200
    body = r.text
    assert "install" in body.lower()
    assert enroll not in body  # token should be a new one
