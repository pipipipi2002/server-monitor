"""Process-wide settings, sourced from env vars."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    monitor_host: str = ""
    display_tz: str = ""
    enrollment_token_ttl: int = 0
    db_path: str = ""

    def __post_init__(self) -> None:
        self.monitor_host = os.environ.get("MONITOR_HOST", "monitor.lan")
        self.display_tz = os.environ.get("DISPLAY_TZ", "UTC")
        self.enrollment_token_ttl = int(os.environ.get("ENROLLMENT_TOKEN_TTL", "3600"))
        self.db_path = os.environ.get("DB_PATH", "/data/server-monitor.sqlite")
