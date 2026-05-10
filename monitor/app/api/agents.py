"""Agent-facing endpoints: admin pending-server creation, enroll, report.

Report ingestion is added in Task 2.5; this task covers admin + enroll only.
"""

from __future__ import annotations

import json
import os as _os
import sqlite3
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.sse import broadcaster
from app.config import Settings
from app.core.clock import now_iso
from app.core.servers import EnrollmentError, authenticate_agent, complete_enrollment, create_pending_server
from app.core.sessions import SessionInput, apply_snapshot
from app.deps import get_db, get_settings


router = APIRouter(prefix="/api")


class CreatePendingRequest(BaseModel):
    hostname: str = Field(min_length=1, max_length=255)
    os: str = Field(pattern="^(windows|linux)$")


class CreatePendingResponse(BaseModel):
    server_id: int
    enrollment_token: str


class EnrollRequest(BaseModel):
    hostname: str
    enrollment_token: str


class EnrollResponse(BaseModel):
    agent_token: str


@router.post(
    "/admin/server",
    response_model=CreatePendingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_pending(
    body: CreatePendingRequest,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CreatePendingResponse:
    sid, token = create_pending_server(
        conn, hostname=body.hostname, os=body.os, ttl_seconds=settings.enrollment_token_ttl
    )
    return CreatePendingResponse(server_id=sid, enrollment_token=token)


@router.post("/enroll", response_model=EnrollResponse)
def enroll(
    body: EnrollRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> EnrollResponse:
    try:
        token = complete_enrollment(
            conn, hostname=body.hostname, enrollment_token=body.enrollment_token
        )
    except EnrollmentError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    return EnrollResponse(agent_token=token)


class ReportRequest(BaseModel):
    hostname: str
    received_at: str | None = None  # monitor stamps if absent
    sessions: list[dict]


def _auth(conn: sqlite3.Connection, hostname: str, header: str | None) -> int:
    if not header or not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = header.split(" ", 1)[1].strip()
    sid = authenticate_agent(conn, hostname=hostname, token=token)
    if sid is None:
        raise HTTPException(status_code=401, detail="invalid token")
    return sid


@router.post("/report")
async def report(
    body: ReportRequest,
    authorization: Annotated[str | None, Header()] = None,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    sid = _auth(conn, body.hostname, authorization)
    received_at = body.received_at or now_iso()
    sessions: list[SessionInput] = [
        {
            "device_name": s["device_name"],
            "username": s.get("username"),
            "protocol": s["protocol"],
            "state": s["state"],
            "logon_at": s["logon_at"],
        }
        for s in body.sessions
    ]
    diff = apply_snapshot(conn, server_id=sid, sessions=sessions, received_at=received_at)
    conn.execute(
        "INSERT INTO reports (server_id, received_at, payload_json) VALUES (?, ?, ?)",
        (sid, received_at, json.dumps({"sessions": body.sessions})),
    )
    await broadcaster.publish(
        {
            "type": "report",
            "hostname": body.hostname,
            "added": diff.added,
            "changed": diff.changed,
            "ended": diff.ended,
            "received_at": received_at,
        }
    )
    return {"accepted": True}


def _binary_name(os_: str, arch: str) -> str:
    if os_ == "windows":
        return "agent-windows.exe"
    if os_ == "linux":
        return f"agent-linux-{arch}"
    raise HTTPException(status_code=400, detail="unsupported os")


@router.get("/agent-binary")
def agent_binary(os: str, arch: str = "x86_64") -> FileResponse:
    dist = Path(_os.environ.get("AGENT_DIST_DIR", "/agents-dist"))
    name = _binary_name(os, arch)
    p = dist / name
    if not p.exists():
        raise HTTPException(status_code=404, detail="binary not built yet")
    return FileResponse(p, filename=name, media_type="application/octet-stream")
