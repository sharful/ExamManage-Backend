import math
import uuid
from datetime import date
from typing import Optional

from sqlalchemy import func, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exam import ExamAssignment
from app.models.invigilator import Invigilator, InvigilatorStatus
from app.schemas.invigilator import (
    InvigilatorCreate,
    InvigilatorListResponse,
    InvigilatorResponse,
    InvigilatorUpdate,
    PaginationMeta,
)


def _base_query():
    """Return a select that excludes soft-deleted rows."""
    return select(Invigilator).where(Invigilator.is_deleted.is_(False))


async def create(db: AsyncSession, data: InvigilatorCreate) -> Invigilator:
    inv = Invigilator(
        name=data.name,
        department=data.department,
        institute=data.institute,
        mobile=data.mobile,
        email=data.email,
        status=data.status,
        remarks=data.remarks,
    )
    db.add(inv)
    await db.flush()
    await db.refresh(inv)
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


async def update(
    db: AsyncSession, inv: Invigilator, data: InvigilatorUpdate
) -> Invigilator:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(inv, field, value)
    await db.flush()
    await db.refresh(inv)
    return inv


async def soft_delete(db: AsyncSession, inv: Invigilator) -> None:
    inv.is_deleted = True
    await db.flush()


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
