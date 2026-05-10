"""Settings should pull from env with sane defaults."""

from __future__ import annotations

import pytest


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONITOR_HOST", raising=False)
    monkeypatch.delenv("DISPLAY_TZ", raising=False)
    monkeypatch.delenv("ENROLLMENT_TOKEN_TTL", raising=False)

    from app.config import Settings

    s = Settings()
    assert s.monitor_host == "monitor.lan"
    assert s.display_tz == "UTC"
    assert s.enrollment_token_ttl == 3600


def test_settings_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONITOR_HOST", "mon.example.com")
    monkeypatch.setenv("ENROLLMENT_TOKEN_TTL", "60")

    from app.config import Settings

    s = Settings()
    assert s.monitor_host == "mon.example.com"
    assert s.enrollment_token_ttl == 60
