from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import APIRouter, Depends
from sqlalchemy import func, literal_column, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.exam import Exam, ExamAssignment
from app.models.invigilator import Invigilator, InvigilatorStatus
from app.models.room import Room
from app.models.user import User
from app.schemas.dashboard import (
    DailySnapshot,
    DashboardConflict,
    DashboardResponse,
    DashboardTrends,
)
from app.services import conflict_engine

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    today = datetime.now(timezone.utc).date()
    seven_days_ago = today - timedelta(days=6)
    tomorrow = today + timedelta(days=1)

    # ── Today's primary stats ────────────────────────────────────────────────

    exams_today = await db.scalar(
        select(func.count()).select_from(Exam).where(Exam.exam_date == today)
    ) or 0

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

    # ── Active conflicts (today only — full engine) ──────────────────────────

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

    # ── Next 7 days exam count ───────────────────────────────────────────────

    exams_next_7_days = await db.scalar(
        select(func.count()).select_from(Exam).where(
            Exam.exam_date.between(tomorrow, today + timedelta(days=7))
        )
    ) or 0

    # ── 7-day history (batch GROUP BY queries) ───────────────────────────────

    # a) Exam counts per day
    exam_rows = await db.execute(
        select(Exam.exam_date, func.count().label("cnt"))
        .where(Exam.exam_date.between(seven_days_ago, today))
        .group_by(Exam.exam_date)
    )
    exam_counts: dict = {row.exam_date: row.cnt for row in exam_rows}

    # b) Distinct rooms used per day
    room_rows = await db.execute(
        select(
            Exam.exam_date,
            func.count(ExamAssignment.room_id.distinct()).label("cnt"),
        )
        .select_from(ExamAssignment)
        .join(ExamAssignment.exam)
        .where(Exam.exam_date.between(seven_days_ago, today))
        .group_by(Exam.exam_date)
    )
    room_counts: dict = {row.exam_date: row.cnt for row in room_rows}

    # c) Distinct invigilators assigned per day (union of all 3 role columns)
    head_sq = (
        select(
            ExamAssignment.head_invigilator_id.label("inv_id"),
            Exam.exam_date.label("exam_date"),
        )
        .select_from(ExamAssignment)
        .join(ExamAssignment.exam)
        .where(Exam.exam_date.between(seven_days_ago, today))
    )
    inv1_sq = (
        select(
            ExamAssignment.invigilator1_id.label("inv_id"),
            Exam.exam_date.label("exam_date"),
        )
        .select_from(ExamAssignment)
        .join(ExamAssignment.exam)
        .where(Exam.exam_date.between(seven_days_ago, today))
    )
    inv2_sq = (
        select(
            ExamAssignment.invigilator2_id.label("inv_id"),
            Exam.exam_date.label("exam_date"),
        )
        .select_from(ExamAssignment)
        .join(ExamAssignment.exam)
        .where(
            Exam.exam_date.between(seven_days_ago, today),
            ExamAssignment.invigilator2_id.isnot(None),
        )
    )
    all_inv_sq = union_all(head_sq, inv1_sq, inv2_sq).subquery()
    inv_rows = await db.execute(
        select(
            all_inv_sq.c.exam_date,
            func.count(all_inv_sq.c.inv_id.distinct()).label("cnt"),
        ).group_by(all_inv_sq.c.exam_date)
    )
    inv_counts: dict = {row.exam_date: row.cnt for row in inv_rows}

    # d) Double-booking conflict heuristic per day:
    #    count invigilators appearing in >1 assignment on the same date+slot
    head_slot_sq = (
        select(
            ExamAssignment.head_invigilator_id.label("inv_id"),
            Exam.exam_date.label("exam_date"),
            Exam.time_slot.label("time_slot"),
        )
        .select_from(ExamAssignment)
        .join(ExamAssignment.exam)
        .where(Exam.exam_date.between(seven_days_ago, today))
    )
    inv1_slot_sq = (
        select(
            ExamAssignment.invigilator1_id.label("inv_id"),
            Exam.exam_date.label("exam_date"),
            Exam.time_slot.label("time_slot"),
        )
        .select_from(ExamAssignment)
        .join(ExamAssignment.exam)
        .where(Exam.exam_date.between(seven_days_ago, today))
    )
    inv2_slot_sq = (
        select(
            ExamAssignment.invigilator2_id.label("inv_id"),
            Exam.exam_date.label("exam_date"),
            Exam.time_slot.label("time_slot"),
        )
        .select_from(ExamAssignment)
        .join(ExamAssignment.exam)
        .where(
            Exam.exam_date.between(seven_days_ago, today),
            ExamAssignment.invigilator2_id.isnot(None),
        )
    )
    all_slot_sq = union_all(head_slot_sq, inv1_slot_sq, inv2_slot_sq).subquery()
    # Invigilators with >1 assignment on the same date+slot = double-booked
    dup_sq = (
        select(
            all_slot_sq.c.exam_date.label("exam_date"),
            all_slot_sq.c.inv_id.label("inv_id"),
        )
        .group_by(
            all_slot_sq.c.exam_date,
            all_slot_sq.c.time_slot,
            all_slot_sq.c.inv_id,
        )
        .having(func.count() > 1)
        .subquery()
    )
    conflict_rows = await db.execute(
        select(dup_sq.c.exam_date, func.count().label("cnt")).group_by(
            dup_sq.c.exam_date
        )
    )
    conflict_counts: dict = {row.exam_date: row.cnt for row in conflict_rows}

    # ── Assemble history list (pad missing days with 0) ─────────────────────

    history: list[DailySnapshot] = []
    for i in range(7):
        d = seven_days_ago + timedelta(days=i)
        history.append(
            DailySnapshot(
                date=d.isoformat(),
                exams=exam_counts.get(d, 0),
                invigilators_assigned=inv_counts.get(d, 0),
                rooms_in_use=room_counts.get(d, 0),
                conflicts=conflict_counts.get(d, 0),
            )
        )

    # ── Trends: today vs yesterday ──────────────────────────────────────────

    today_snap = history[-1]
    yesterday_snap = history[-2]
    trends = DashboardTrends(
        exams_delta=today_snap.exams - yesterday_snap.exams,
        rooms_delta=today_snap.rooms_in_use - yesterday_snap.rooms_in_use,
        conflicts_delta=today_snap.conflicts - yesterday_snap.conflicts,
        invigilators_working_today=today_snap.invigilators_assigned,
    )

    return DashboardResponse(
        exams_today=exams_today,
        available_invigilators=available_invigilators,
        unavailable_invigilators=unavailable_invigilators,
        rooms_in_use_today=rooms_in_use_today,
        rooms_free_today=rooms_free_today,
        conflicts=conflicts,
        history=history,
        trends=trends,
        exams_next_7_days=exams_next_7_days,
    )
