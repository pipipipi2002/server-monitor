"""Shared pytest fixtures for the agent test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    """Per-test token file location."""
    p = tmp_path / "token"
    return p
