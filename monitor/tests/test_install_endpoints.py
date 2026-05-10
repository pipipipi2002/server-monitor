async def test_install_sh_served(client) -> None:
    r = await client.get("/install.sh")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/")
    assert "#!/usr/bin/env bash" in r.text


async def test_install_ps1_served(client) -> None:
    r = await client.get("/install.ps1")
    assert r.status_code == 200
    assert "Install-MonitorAgent" in r.text


async def test_uninstall_sh_served(client) -> None:
    r = await client.get("/uninstall.sh")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/")
    assert "#!/usr/bin/env bash" in r.text
    assert "systemctl stop server-monitor-agent" in r.text


async def test_uninstall_ps1_served(client) -> None:
    r = await client.get("/uninstall.ps1")
    assert r.status_code == 200
    assert "Uninstall-MonitorAgent" in r.text


async def test_ca_cert_served_when_present(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CADDY_CA_PATH", str(tmp_path / "ca.pem"))
    (tmp_path / "ca.pem").write_text("-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n")
    r = await client.get("/ca.crt")
    assert r.status_code == 200
    assert b"BEGIN CERTIFICATE" in r.content
