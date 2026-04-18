import math
import uuid
from datetime import date
from typing import Optional

from sqlalchemy import extract, func, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.audit import log_action
from app.models.exam import Exam, ExamAssignment
from app.models.invigilator import Invigilator, InvigilatorStatus
from app.models.room import Room
from app.schemas.invigilator import (
    AffectedAssignmentInfo,
    InvigilatorCreate,
    InvigilatorListResponse,
    InvigilatorResponse,
    InvigilatorUpdate,
    InvigilatorWorkloadResponse,
    PaginationMeta,
    WorkloadMonthEntry,
    WorkloadSummaryItem,
    WorkloadSummaryResponse,
)

_FIELDS = ("name", "department", "institute", "designation", "mobile", "email", "status", "remarks")


def _snapshot(inv: Invigilator) -> dict:
    return {f: getattr(inv, f) for f in _FIELDS}


def _base_query():
    """Return a select that excludes soft-deleted rows."""
    return select(Invigilator).where(Invigilator.is_deleted.is_(False))


async def create(
    db: AsyncSession,
    data: InvigilatorCreate,
    user_id: uuid.UUID | None = None,
) -> Invigilator:
    inv = Invigilator(
        name=data.name,
        department=data.department,
        institute=data.institute,
        designation=data.designation,
        mobile=data.mobile,
        email=data.email,
        status=data.status,
        remarks=data.remarks,
    )
    db.add(inv)
    await db.flush()
    await db.refresh(inv)
    await log_action(
        db,
        user_id=user_id,
        action="create",
        entity_type="invigilator",
        entity_id=inv.id,
        old_values=None,
        new_values=_snapshot(inv),
    )
    return inv


async def get_by_id(db: AsyncSession, inv_id: uuid.UUID) -> Optional[Invigilator]:
    result = await db.execute(
        _base_query().where(Invigilator.id == inv_id)
    )
    return result.scalar_one_or_none()


async def get_list(
    db: AsyncSession,
    *,
    search: Optional[str] = None,
    department: Optional[str] = None,
    status: Optional[InvigilatorStatus] = None,
    page: int = 1,
    limit: int = 20,
) -> InvigilatorListResponse:
    q = _base_query()

    if search:
        q = q.where(Invigilator.name.ilike(f"%{search}%"))
    if department:
        q = q.where(Invigilator.department.ilike(f"%{department}%"))
    if status:
        q = q.where(Invigilator.status == status)

    # Count total matching rows
    count_q = select(func.count()).select_from(q.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    # Fetch page
    offset = (page - 1) * limit
    rows = (
        await db.execute(q.order_by(Invigilator.name).offset(offset).limit(limit))
    ).scalars().all()

    pages = math.ceil(total / limit) if limit else 1

    return InvigilatorListResponse(
        data=[InvigilatorResponse.model_validate(r) for r in rows],
        meta=PaginationMeta(page=page, limit=limit, total=total, pages=pages),
    )


async def get_future_assignments(
    db: AsyncSession, inv_id: uuid.UUID
) -> list[AffectedAssignmentInfo]:
    """
    Return upcoming assignments (exam_date >= today) for an invigilator in any role.
    Used to warn admins before marking an invigilator unavailable.
    """
    today = date.today()

    rows = (
        await db.execute(
            select(
                ExamAssignment.id,
                ExamAssignment.exam_id,
                ExamAssignment.room_id,
                ExamAssignment.head_invigilator_id,
                ExamAssignment.invigilator1_id,
                ExamAssignment.invigilator2_id,
                Exam.exam_name,
                Exam.exam_date,
                Room.room_number,
            )
            .join(Exam, ExamAssignment.exam_id == Exam.id)
            .join(Room, ExamAssignment.room_id == Room.id)
            .where(
                Exam.exam_date >= today,
                or_(
                    ExamAssignment.head_invigilator_id == inv_id,
                    ExamAssignment.invigilator1_id == inv_id,
                    ExamAssignment.invigilator2_id == inv_id,
                ),
            )
            .order_by(Exam.exam_date)
        )
    ).all()

    result: list[AffectedAssignmentInfo] = []
    for row in rows:
        if row.head_invigilator_id == inv_id:
            role = "head"
        elif row.invigilator1_id == inv_id:
            role = "invigilator1"
        else:
            role = "invigilator2"
        result.append(
            AffectedAssignmentInfo(
                assignment_id=row.id,
                exam_id=row.exam_id,
                exam_name=row.exam_name,
                exam_date=row.exam_date,
                room_id=row.room_id,
                room_number=row.room_number,
                role=role,
            )
        )
    return result


async def update(
    db: AsyncSession,
    inv: Invigilator,
    data: InvigilatorUpdate,
    user_id: uuid.UUID | None = None,
) -> tuple[Invigilator, list[AffectedAssignmentInfo]]:
    updates = data.model_dump(exclude_unset=True)

    # Check for future assignments when changing status to unavailable
    affected: list[AffectedAssignmentInfo] = []
    going_unavailable = (
        "status" in updates
        and updates["status"] == InvigilatorStatus.unavailable
        and inv.status != InvigilatorStatus.unavailable
    )
    if going_unavailable:
        affected = await get_future_assignments(db, inv.id)

    old_values = {f: getattr(inv, f) for f in updates}
    for field, value in updates.items():
        setattr(inv, field, value)
    await db.flush()
    await db.refresh(inv)
    new_values = {f: getattr(inv, f) for f in updates}
    await log_action(
        db,
        user_id=user_id,
        action="update",
        entity_type="invigilator",
        entity_id=inv.id,
        old_values=old_values,
        new_values=new_values,
    )
    return inv, affected


async def soft_delete(
    db: AsyncSession,
    inv: Invigilator,
    user_id: uuid.UUID | None = None,
) -> None:
    old_values = _snapshot(inv)
    inv.is_deleted = True
    await db.flush()
    await log_action(
        db,
        user_id=user_id,
        action="delete",
        entity_type="invigilator",
        entity_id=inv.id,
        old_values=old_values,
        new_values=None,
    )


async def get_workload(
    db: AsyncSession, inv_id: uuid.UUID
) -> Optional[InvigilatorWorkloadResponse]:
    """
    Return workload data for a single invigilator: total assignment appearances,
    breakdown by month/year, and list of all assigned exam dates.
    """
    inv = await get_by_id(db, inv_id)
    if inv is None:
        return None

    # All exam_assignment rows where this invigilator appears in any role
    assignment_q = (
        select(ExamAssignment.id, Exam.exam_date)
        .join(Exam, ExamAssignment.exam_id == Exam.id)
        .where(
            or_(
                ExamAssignment.head_invigilator_id == inv_id,
                ExamAssignment.invigilator1_id == inv_id,
                ExamAssignment.invigilator2_id == inv_id,
            )
        )
        .order_by(Exam.exam_date)
    )
    rows = (await db.execute(assignment_q)).all()

    total_assignments = len(rows)

    # Group by year/month and collect unique dates
    month_map: dict[tuple[int, int], int] = {}
    seen_dates: set[date] = set()
    for _, exam_date in rows:
        key = (exam_date.year, exam_date.month)
        month_map[key] = month_map.get(key, 0) + 1
        seen_dates.add(exam_date)

    assignments_by_month = [
        WorkloadMonthEntry(year=y, month=m, count=c)
        for (y, m), c in sorted(month_map.items())
    ]

    return InvigilatorWorkloadResponse(
        invigilator_id=inv_id,
        name=inv.name,
        total_assignments=total_assignments,
        assignments_by_month=assignments_by_month,
        assigned_dates=sorted(seen_dates),
    )


async def get_workload_summary(
    db: AsyncSession, month: int, year: int
) -> WorkloadSummaryResponse:
    """
    Return all (non-deleted) invigilators with their assignment counts for the
    given month/year, sorted by count descending.
    """
    # Union all three invigilator roles for the given month/year
    role_sq = union(
        select(ExamAssignment.head_invigilator_id.label("inv_id"))
        .join(Exam, ExamAssignment.exam_id == Exam.id)
        .where(
            extract("month", Exam.exam_date) == month,
            extract("year", Exam.exam_date) == year,
        ),
        select(ExamAssignment.invigilator1_id.label("inv_id"))
        .join(Exam, ExamAssignment.exam_id == Exam.id)
        .where(
            extract("month", Exam.exam_date) == month,
            extract("year", Exam.exam_date) == year,
        ),
        select(ExamAssignment.invigilator2_id.label("inv_id"))
        .join(Exam, ExamAssignment.exam_id == Exam.id)
        .where(
            extract("month", Exam.exam_date) == month,
            extract("year", Exam.exam_date) == year,
            ExamAssignment.invigilator2_id.is_not(None),
        ),
    ).subquery()

    count_sq = (
        select(role_sq.c.inv_id, func.count().label("cnt"))
        .group_by(role_sq.c.inv_id)
    ).subquery()

    q = (
        select(Invigilator, func.coalesce(count_sq.c.cnt, 0).label("cnt"))
        .outerjoin(count_sq, Invigilator.id == count_sq.c.inv_id)
        .where(Invigilator.is_deleted.is_(False))
        .order_by(func.coalesce(count_sq.c.cnt, 0).desc(), Invigilator.name)
    )

    rows = (await db.execute(q)).all()

    return WorkloadSummaryResponse(
        month=month,
        year=year,
        data=[
            WorkloadSummaryItem(
                invigilator_id=inv.id,
                name=inv.name,
                assignment_count=cnt,
            )
            for inv, cnt in rows
        ],
    )


async def get_available_for_date(
    db: AsyncSession, target_date: date
) -> list[Invigilator]:
    """
    Return invigilators whose status is 'available' and who are not already
    assigned (in any role) to any exam on target_date.
    """
    # Sub-query: IDs of invigilators assigned on that date (any role)
    assigned_sq = union(
        select(ExamAssignment.head_invigilator_id.label("inv_id"))
        .join(ExamAssignment.exam)
        .where(ExamAssignment.exam.has(exam_date=target_date)),
        select(ExamAssignment.invigilator1_id.label("inv_id"))
        .join(ExamAssignment.exam)
        .where(ExamAssignment.exam.has(exam_date=target_date)),
        select(ExamAssignment.invigilator2_id.label("inv_id"))
        .join(ExamAssignment.exam)
        .where(
            ExamAssignment.exam.has(exam_date=target_date),
            ExamAssignment.invigilator2_id.is_not(None),
        ),
    ).subquery()

    q = (
        _base_query()
        .where(Invigilator.status == InvigilatorStatus.available)
        .where(Invigilator.id.not_in(select(assigned_sq.c.inv_id)))
        .order_by(Invigilator.name)
    )

    result = await db.execute(q)
    return result.scalars().all()
