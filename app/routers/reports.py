"""
Report endpoints — Section 4.7.

GET /api/reports/duty-list?date=YYYY-MM-DD&format=pdf|excel
GET /api/reports/room-schedule?date=YYYY-MM-DD&format=pdf|excel
GET /api/reports/daily-schedule?date=YYYY-MM-DD&format=pdf|excel
GET /api/reports/export?type=duty-list|room-schedule|daily-schedule&date=YYYY-MM-DD&format=pdf|excel
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.services import report_service as svc

router = APIRouter(prefix="/api/reports", tags=["reports"])

ReportFormat = Literal["pdf", "excel"]
ReportType = Literal["duty-list", "room-schedule", "daily-schedule"]

_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _streaming_response(data: bytes, filename: str, fmt: str) -> StreamingResponse:
    from io import BytesIO

    return StreamingResponse(
        BytesIO(data),
        media_type=_CONTENT_TYPES[fmt],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/duty-list")
async def duty_list(
    report_date: date = Query(..., alias="date", description="Report date (YYYY-MM-DD)"),
    format: ReportFormat = Query("pdf"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    data, filename = await svc.generate_duty_list(db, report_date, format)
    return _streaming_response(data, filename, format)


@router.get("/room-schedule")
async def room_schedule(
    report_date: date = Query(..., alias="date", description="Report date (YYYY-MM-DD)"),
    format: ReportFormat = Query("pdf"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    data, filename = await svc.generate_room_schedule(db, report_date, format)
    return _streaming_response(data, filename, format)


@router.get("/daily-schedule")
async def daily_schedule(
    report_date: date = Query(..., alias="date", description="Report date (YYYY-MM-DD)"),
    format: ReportFormat = Query("pdf"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    data, filename = await svc.generate_daily_schedule(db, report_date, format)
    return _streaming_response(data, filename, format)


@router.get("/preview")
async def preview_report(
    type: ReportType = Query(..., description="Report type"),
    report_date: date = Query(..., alias="date", description="Report date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return await svc.get_preview_data(db, type, report_date)


@router.get("/export")
async def export(
    type: ReportType = Query(..., description="Report type"),
    report_date: date = Query(..., alias="date", description="Report date (YYYY-MM-DD)"),
    format: ReportFormat = Query("pdf"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    generators = {
        "duty-list": svc.generate_duty_list,
        "room-schedule": svc.generate_room_schedule,
        "daily-schedule": svc.generate_daily_schedule,
    }
    data, filename = await generators[type](db, report_date, format)
    return _streaming_response(data, filename, format)
