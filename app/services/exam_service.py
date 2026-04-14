import math
import uuid
from datetime import date
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.exam import Exam, ExamAssignment
from app.schemas.exam import (
    ExamCreate,
    ExamListResponse,
    ExamResponse,
    ExamUpdate,
    PaginationMeta,
)


async def create(db: AsyncSession, data: ExamCreate) -> Exam:
    exam = Exam(
        exam_name=data.exam_name,
        exam_date=data.exam_date,
        time_slot=data.time_slot,
    )
    db.add(exam)
    await db.flush()
    await db.refresh(exam)
    return exam


async def get_by_id(db: AsyncSession, exam_id: uuid.UUID) -> Optional[Exam]:
    result = await db.execute(
        select(Exam)
        .options(selectinload(Exam.assignments))
        .where(Exam.id == exam_id)
    )
    return result.scalar_one_or_none()


async def get_list(
    db: AsyncSession,
    *,
    name: Optional[str] = None,
    exam_date: Optional[date] = None,
    room_id: Optional[uuid.UUID] = None,
    page: int = 1,
    limit: int = 20,
) -> ExamListResponse:
    q = select(Exam)

    if name:
        q = q.where(Exam.exam_name.ilike(f"%{name}%"))
    if exam_date:
        q = q.where(Exam.exam_date == exam_date)
    if room_id:
        q = q.where(
            Exam.id.in_(
                select(ExamAssignment.exam_id).where(
                    ExamAssignment.room_id == room_id
                )
            )
        )

    count_q = select(func.count()).select_from(q.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    offset = (page - 1) * limit
    rows = (
        await db.execute(
            q.order_by(Exam.exam_date.desc(), Exam.exam_name).offset(offset).limit(limit)
        )
    ).scalars().all()

    pages = math.ceil(total / limit) if limit else 1

    return ExamListResponse(
        data=[ExamResponse.model_validate(r) for r in rows],
        meta=PaginationMeta(page=page, limit=limit, total=total, pages=pages),
    )


async def update(db: AsyncSession, exam: Exam, data: ExamUpdate) -> Exam:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(exam, field, value)
    await db.flush()
    await db.refresh(exam)
    return exam


async def delete(db: AsyncSession, exam: Exam) -> None:
    # Cascade delete of assignments is handled by the ORM relationship
    await db.delete(exam)
    await db.flush()
