"""Pure snapshot helpers used by the agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict


class Session(TypedDict):
    device_name: str
    username: str | None
    protocol: str  # 'rdp' | 'ssh' | 'console'
    state: str  # 'active' | 'disconnected'
    logon_at: str  # ISO-8601 UTC


@dataclass
class Diff:
    added: list[Session] = field(default_factory=list)
    removed: list[Session] = field(default_factory=list)
    changed: list[Session] = field(default_factory=list)


def _by_device(items: list[Session]) -> dict[str, Session]:
    return {s["device_name"]: s for s in items}


def diff_snapshots(prev: list[Session], curr: list[Session]) -> Diff:
    p, c = _by_device(prev), _by_device(curr)
    out = Diff()
    for k, v in c.items():
        if k not in p:
            out.added.append(v)
        elif v != p[k]:
            out.changed.append(v)
    for k, v in p.items():
        if k not in c:
            out.removed.append(v)
    return out


def is_changed(prev: list[Session], curr: list[Session]) -> bool:
    d = diff_snapshots(prev, curr)
    return bool(d.added or d.removed or d.changed)
