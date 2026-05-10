from pathlib import Path


def test_systemd_unit_has_required_sections() -> None:
    text = Path("agent/installers/server-monitor-agent.service").read_text()
    assert "[Unit]" in text
    assert "[Service]" in text
    assert "[Install]" in text
    assert "ExecStart=" in text
    assert "Restart=on-failure" in text
