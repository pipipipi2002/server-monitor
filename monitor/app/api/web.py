"""Server-rendered web pages."""

from __future__ import annotations

import os as _os
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.core.aliases import get_alias
from app.core.aliases import known_members as _known_members
from app.core.aliases import list_aliases as _list_aliases
from app.core.bookings import list_bookings_for_day
from app.core.clock import now, parse_iso
from app.core.servers import create_pending_server, list_servers as _list_servers, reset_server
from app.core.sessions import list_active_sessions
from app.deps import get_db, get_settings


_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


def _server_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    sessions = list_active_sessions(conn, server_id=row["id"])
    enriched = []
    for s in sessions:
        enriched.append(
            {
                "device_name": s["device_name"],
                "alias": get_alias(conn, s["device_name"]),
                "state": s["state"],
                "logon_at": s["logon_at"],
                "protocol": s["protocol"],
                "username": s["username"],
            }
        )
    online = parse_iso(row["last_seen_at"]) >= now() - timedelta(seconds=60) if row["last_seen_at"] else False
    return {
        "id": row["id"],
        "hostname": row["hostname"],
        "os": row["os"],
        "online": online,
        "enrolled": bool(row["enrolled"]),
        "last_seen_at": row["last_seen_at"],
        "sessions": enriched,
    }


router = APIRouter(default_response_class=HTMLResponse)


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    fragment: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    servers = [_server_view(conn, r) for r in _list_servers(conn)]
    if fragment == "grid":
        return _TEMPLATES.TemplateResponse(
            request,
            "_partials/server_grid.html",
            {"servers": servers},
        )
    return _TEMPLATES.TemplateResponse(
        request,
        "dashboard.html",
        {"display_tz": settings.display_tz, "servers": servers},
    )


@router.get("/aliases", response_class=HTMLResponse)
def aliases_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return _TEMPLATES.TemplateResponse(
        request,
        "aliases.html",
        {
            "display_tz": settings.display_tz,
            "rows": [dict(r) for r in _list_aliases(conn)],
        },
    )


def _server_or_404(conn: sqlite3.Connection, server_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM servers WHERE id=?", (server_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return row


def _day_or_today(day_str: str | None) -> date:
    if not day_str:
        return datetime.now(UTC).date()
    try:
        return date.fromisoformat(day_str)
    except ValueError as e:
        raise HTTPException(status_code=422, detail="invalid day") from e


@router.get("/server/{server_id}", response_class=HTMLResponse)
def server_detail(
    server_id: int,
    request: Request,
    day: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    row = _server_or_404(conn, server_id)
    d = _day_or_today(day)
    bookings = list_bookings_for_day(conn, server_id=server_id, day=d)
    by_start = {b["start_at"]: dict(b) for b in bookings}

    base = datetime(d.year, d.month, d.day, tzinfo=UTC)
    slots = []
    for i in range(48):
        start = base + timedelta(minutes=30 * i)
        slots.append({
            "start_at": start.isoformat(),
            "label": start.strftime("%H:%M"),
            "booking": by_start.get(start.isoformat()),
            "is_past": start < datetime.now(UTC),
        })

    days = [(datetime.now(UTC).date() + timedelta(days=i)) for i in range(7)]

    return _TEMPLATES.TemplateResponse(
        request,
        "server_detail.html",
        {
            "display_tz": settings.display_tz,
            "server": dict(row),
            "day": d.isoformat(),
            "slots": slots,
            "days": [x.isoformat() for x in days],
            "members": _known_members(conn),
        },
    )


@router.get("/enroll", response_class=HTMLResponse)
def enroll_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return _TEMPLATES.TemplateResponse(
        request,
        "enroll.html",
        {
            "display_tz": settings.display_tz,
            "monitor_host": settings.monitor_host,
            "rows": [dict(r) for r in _list_servers(conn)],
            "command": None,
            "selected_os": None,
        },
    )


@router.post("/enroll", response_class=HTMLResponse)
def enroll_create(
    request: Request,
    hostname: str = Form(...),
    os: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    sid, token = create_pending_server(
        conn, hostname=hostname, os=os, ttl_seconds=settings.enrollment_token_ttl
    )
    return _TEMPLATES.TemplateResponse(
        request,
        "enroll.html",
        {
            "display_tz": settings.display_tz,
            "monitor_host": settings.monitor_host,
            "rows": [dict(r) for r in _list_servers(conn)],
            "command": {"hostname": hostname, "os": os, "token": token},
            "selected_os": os,
        },
    )


# ---------------------------------------------------------------------------
# Install-script endpoints
# ---------------------------------------------------------------------------

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/install.sh", response_class=PlainTextResponse)
def install_sh() -> PlainTextResponse:
    return PlainTextResponse(
        (_STATIC_DIR / "install.sh").read_text(),
        media_type="text/x-shellscript; charset=utf-8",
    )


@router.get("/install.ps1", response_class=PlainTextResponse)
def install_ps1() -> PlainTextResponse:
    return PlainTextResponse(
        (_STATIC_DIR / "install.ps1").read_text(),
        media_type="text/plain; charset=utf-8",
    )


@router.get("/ca.crt")
def ca_crt():
    p = Path(_os.environ.get("CADDY_CA_PATH", "/caddy/data/caddy/pki/authorities/local/root.crt"))
    if not p.exists():
        raise HTTPException(status_code=404, detail="CA not provisioned yet")
    return FileResponse(p, media_type="application/x-x509-ca-cert", filename="ca.crt")


@router.post("/enroll/{server_id}/reset", response_class=HTMLResponse)
def reset_server_endpoint(
    server_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    row = _server_or_404(conn, server_id)
    token = reset_server(conn, server_id=server_id, ttl_seconds=settings.enrollment_token_ttl)
    return _TEMPLATES.TemplateResponse(
        request,
        "enroll.html",
        {
            "display_tz": settings.display_tz,
            "monitor_host": settings.monitor_host,
            "rows": [dict(r) for r in _list_servers(conn)],
            "command": {"hostname": row["hostname"], "os": row["os"], "token": token},
            "selected_os": row["os"],
        },
    )
