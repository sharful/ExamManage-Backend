from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.exam import Exam, ExamAssignment
from app.models.invigilator import Invigilator, InvigilatorStatus
from app.models.room import Room
from app.models.user import User
from app.schemas.dashboard import DashboardConflict, DashboardResponse
from app.services import conflict_engine

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    today = datetime.now(timezone.utc).date()

    # Exams today
    exams_today = await db.scalar(
        select(func.count()).select_from(Exam).where(Exam.exam_date == today)
    ) or 0

    # Invigilator counts (non-deleted only)
    available_invigilators = await db.scalar(
        select(func.count()).select_from(Invigilator).where(
            Invigilator.is_deleted.is_(False),
            Invigilator.status == InvigilatorStatus.available,
        )
    ) or 0

    unavailable_invigilators = await db.scalar(
        select(func.count()).select_from(Invigilator).where(
            Invigilator.is_deleted.is_(False),
            Invigilator.status == InvigilatorStatus.unavailable,
        )
    ) or 0

    # Rooms in use today — distinct rooms assigned to any exam today
    rooms_in_use_sq = (
        select(ExamAssignment.room_id)
        .join(ExamAssignment.exam)
        .where(Exam.exam_date == today)
        .distinct()
        .subquery()
    )
    rooms_in_use_today = await db.scalar(
        select(func.count()).select_from(rooms_in_use_sq)
    ) or 0

    total_rooms = await db.scalar(select(func.count()).select_from(Room)) or 0
    rooms_free_today = total_rooms - rooms_in_use_today

    # Active conflicts for today — scan each assignment and collect violations
    assignments_result = await db.execute(
        select(ExamAssignment)
        .join(ExamAssignment.exam)
        .where(Exam.exam_date == today)
        .options(selectinload(ExamAssignment.exam))
    )
    assignments = assignments_result.scalars().all()

    seen: set[tuple] = set()
    conflicts: list[DashboardConflict] = []

    for assignment in assignments:
        candidate = SimpleNamespace(
            exam_id=assignment.exam_id,
            room_id=assignment.room_id,
            seats=assignment.seats,
            head_invigilator_id=assignment.head_invigilator_id,
            invigilator1_id=assignment.invigilator1_id,
            invigilator2_id=assignment.invigilator2_id,
        )
        errs = await conflict_engine.validate_assignment(
            candidate, db, exclude_assignment_id=assignment.id
        )
        for err in errs:
            key = (err.type, str(err.invigilator_id), str(assignment.exam_id))
            if key not in seen:
                seen.add(key)
                conflicts.append(
                    DashboardConflict(
                        type=err.type,
                        message=err.message,
                        exam_id=assignment.exam_id,
                        exam_name=assignment.exam.exam_name,
                        invigilator_id=err.invigilator_id,
                        details=err.details,
                    )
                )

    return DashboardResponse(
        exams_today=exams_today,
        available_invigilators=available_invigilators,
        unavailable_invigilators=unavailable_invigilators,
        rooms_in_use_today=rooms_in_use_today,
        rooms_free_today=rooms_free_today,
        conflicts=conflicts,
    )
