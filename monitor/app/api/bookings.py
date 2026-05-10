"""Bookings API: create, list-for-day, delete. No auth (open on the LAN)."""

from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.api.sse import broadcaster
from app.core.bookings import (
    BookingConflict,
    BookingError,
    create_booking,
    delete_booking,
    list_bookings_for_day,
)
from app.core.clock import parse_iso
from app.deps import get_db


router = APIRouter()


class CreateBookingRequest(BaseModel):
    server_id: int
    start_at: str
    member_name: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=200)


@router.post("/bookings", status_code=status.HTTP_201_CREATED)
async def create(
    body: CreateBookingRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    start = parse_iso(body.start_at)
    try:
        bid = create_booking(
            conn,
            server_id=body.server_id,
            start_at=start,
            member_name=body.member_name,
            note=body.note,
        )
    except BookingConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except BookingError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    await broadcaster.publish(
        {
            "type": "booking.created",
            "id": bid,
            "server_id": body.server_id,
            "start_at": start.isoformat(),
            "member_name": body.member_name,
        }
    )
    return {"id": bid}


@router.get("/bookings")
def list_for_day(
    server_id: int = Query(...),
    day: str = Query(..., description="YYYY-MM-DD"),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        d = date.fromisoformat(day)
    except ValueError as e:
        raise HTTPException(status_code=422, detail="invalid day") from e
    rows = list_bookings_for_day(conn, server_id=server_id, day=d)
    return {"items": [dict(r) for r in rows]}


@router.delete("/bookings/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    booking_id: int,
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    if not delete_booking(conn, booking_id):
        raise HTTPException(status_code=404, detail="not found")
    await broadcaster.publish({"type": "booking.deleted", "id": booking_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
