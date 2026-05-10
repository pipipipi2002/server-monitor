"""Public, editable alias map. No auth.

GET  /api/aliases       → JSON list (consumed by older clients/tests)
GET  /api/aliases/members → JSON list of distinct alias values
POST /aliases            → form-or-JSON upsert; returns row partial on HTMX, 303 redirect on plain
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.api.sse import broadcaster
from app.core.aliases import known_members, list_aliases, upsert_alias
from app.deps import get_db


_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


router = APIRouter()


class UpsertAliasRequest(BaseModel):
    device_name: str = Field(min_length=1, max_length=255)
    alias: str = Field(min_length=1, max_length=64)


async def _do_upsert(conn: sqlite3.Connection, device_name: str, alias: str) -> str:
    try:
        upsert_alias(conn, device_name=device_name, alias=alias)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    cleaned = alias.strip()
    await broadcaster.publish(
        {"type": "alias.updated", "device_name": device_name, "alias": cleaned}
    )
    return cleaned


@router.post("/aliases")
async def upsert(
    request: Request,
    hx_request: str | None = Header(default=None, alias="HX-Request"),
    conn: sqlite3.Connection = Depends(get_db),
):
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        body = UpsertAliasRequest.model_validate(await request.json())
        cleaned = await _do_upsert(conn, body.device_name, body.alias)
        return {"device_name": body.device_name, "alias": cleaned}

    form = await request.form()
    device_name = (form.get("device_name") or "").strip()
    alias = (form.get("alias") or "").strip()
    if not device_name or not alias:
        raise HTTPException(status_code=422, detail="device_name and alias required")
    cleaned = await _do_upsert(conn, device_name, alias)

    if hx_request == "true":
        return _TEMPLATES.TemplateResponse(
            request,
            "_partials/alias_row.html",
            {
                "row": {"device_name": device_name, "alias": cleaned, "updated_at": ""},
            },
        )
    return RedirectResponse(url="/aliases", status_code=303)


@router.get("/api/aliases")
def list_all(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return {"items": [dict(r) for r in list_aliases(conn)]}


@router.get("/api/aliases/members")
def members(conn: sqlite3.Connection = Depends(get_db)) -> dict:
    return {"members": known_members(conn)}
