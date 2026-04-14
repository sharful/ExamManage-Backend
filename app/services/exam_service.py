import math
import uuid
from datetime import date
from types import SimpleNamespace
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.middleware.audit import log_action
from app.models.exam import Exam, ExamAssignment
from app.schemas.exam import (
    AssignmentResponse,
    ClonedAssignmentResult,
    ExamCloneResponse,
    ExamCreate,
    ExamListResponse,
    ExamResponse,
    ExamUpdate,
    PaginationMeta,
)
from app.services import conflict_engine


def _snapshot(exam: Exam) -> dict:
    return {
        "exam_name": exam.exam_name,
        "exam_date": exam.exam_date,
        "time_slot": exam.time_slot,
    }


async def create(
    db: AsyncSession,
    data: ExamCreate,
    user_id: uuid.UUID | None = None,
) -> Exam:
    exam = Exam(
        exam_name=data.exam_name,
        exam_date=data.exam_date,
        time_slot=data.time_slot,
    )
    db.add(exam)
    await db.flush()
    await db.refresh(exam)
    await log_action(
        db,
        user_id=user_id,
        action="create",
        entity_type="exam",
        entity_id=exam.id,
        old_values=None,
        new_values=_snapshot(exam),
    )
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


async def update(
    db: AsyncSession,
    exam: Exam,
    data: ExamUpdate,
    user_id: uuid.UUID | None = None,
) -> Exam:
    updates = data.model_dump(exclude_unset=True)
    old_values = {f: getattr(exam, f) for f in updates}
    for field, value in updates.items():
        setattr(exam, field, value)
    await db.flush()
    await db.refresh(exam)
    new_values = {f: getattr(exam, f) for f in updates}
    await log_action(
        db,
        user_id=user_id,
        action="update",
        entity_type="exam",
        entity_id=exam.id,
        old_values=old_values,
        new_values=new_values,
    )
    return exam


async def clone(
    db: AsyncSession,
    source_id: uuid.UUID,
    new_exam_name: str,
    new_date: date,
    user_id: uuid.UUID | None = None,
) -> Optional[ExamCloneResponse]:
    """
    Clone an existing exam to a new date.  All assignments are copied verbatim;
    conflict validation is run on each BEFORE any assignment is persisted so
    that within-exam cross-checks don't produce false positives.  Assignments
    are created regardless of conflicts — conflicts are flagged in the response.
    """
    source = await get_by_id(db, source_id)
    if source is None:
        return None

    # Create the cloned exam
    new_exam = Exam(
        exam_name=new_exam_name,
        exam_date=new_date,
        time_slot=source.time_slot,
    )
    db.add(new_exam)
    await db.flush()
    await db.refresh(new_exam)
    await log_action(
        db,
        user_id=user_id,
        action="create",
        entity_type="exam",
        entity_id=new_exam.id,
        old_values=None,
        new_values=_snapshot(new_exam),
    )

    # Phase 1: validate ALL candidates before creating any assignments,
    # so within-exam assignments don't flag each other as conflicts.
    candidates = []
    for src_a in source.assignments:
        candidate = SimpleNamespace(
            exam_id=new_exam.id,
            room_id=src_a.room_id,
            seats=src_a.seats,
            head_invigilator_id=src_a.head_invigilator_id,
            invigilator1_id=src_a.invigilator1_id,
            invigilator2_id=src_a.invigilator2_id,
        )
        errors = await conflict_engine.validate_assignment(candidate, db)
        candidates.append((src_a, errors))

    # Phase 2: create all assignments (regardless of conflicts)
    _ASSIGN_FIELDS = (
        "exam_id", "room_id", "seats",
        "head_invigilator_id", "invigilator1_id", "invigilator2_id",
    )
    cloned_results: list[ClonedAssignmentResult] = []
    for src_a, errors in candidates:
        new_a = ExamAssignment(
            exam_id=new_exam.id,
            room_id=src_a.room_id,
            seats=src_a.seats,
            head_invigilator_id=src_a.head_invigilator_id,
            invigilator1_id=src_a.invigilator1_id,
            invigilator2_id=src_a.invigilator2_id,
        )
        db.add(new_a)
        await db.flush()
        await db.refresh(new_a)
        await log_action(
            db,
            user_id=user_id,
            action="create",
            entity_type="assignment",
            entity_id=new_a.id,
            old_values=None,
            new_values={f: getattr(new_a, f) for f in _ASSIGN_FIELDS},
        )
        cloned_results.append(
            ClonedAssignmentResult(
                assignment=AssignmentResponse.model_validate(new_a),
                has_conflicts=len(errors) > 0,
                conflicts=errors,
            )
        )

    conflict_count = sum(1 for r in cloned_results if r.has_conflicts)

    return ExamCloneResponse(
        exam=ExamResponse.model_validate(new_exam),
        assignments=cloned_results,
        total_assignments=len(cloned_results),
        conflict_count=conflict_count,
    )


async def delete(
    db: AsyncSession,
    exam: Exam,
    user_id: uuid.UUID | None = None,
) -> None:
    # Cascade delete of assignments is handled by the ORM relationship
    old_values = _snapshot(exam)
    entity_id = exam.id
    await db.delete(exam)
    await db.flush()
    await log_action(
        db,
        user_id=user_id,
        action="delete",
        entity_type="exam",
        entity_id=entity_id,
        old_values=old_values,
        new_values=None,
    )
