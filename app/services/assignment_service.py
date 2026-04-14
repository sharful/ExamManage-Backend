import uuid
from datetime import date
from types import SimpleNamespace
from typing import Optional

from sqlalchemy import select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exam import Exam, ExamAssignment, TimeSlot
from app.models.invigilator import Invigilator, InvigilatorStatus
from app.schemas.exam import AssignmentCreate, AssignmentUpdate, ConflictError
from app.services import conflict_engine


async def create(
    db: AsyncSession, data: AssignmentCreate
) -> tuple[Optional[ExamAssignment], list[ConflictError]]:
    """
    Run conflict validation, then create the assignment.
    Returns (assignment, []) on success or (None, errors) on conflict.
    """
    errors = await conflict_engine.validate_assignment(data, db)
    if errors:
        return None, errors

    assignment = ExamAssignment(
        exam_id=data.exam_id,
        room_id=data.room_id,
        seats=data.seats,
        head_invigilator_id=data.head_invigilator_id,
        invigilator1_id=data.invigilator1_id,
        invigilator2_id=data.invigilator2_id,
    )
    db.add(assignment)
    await db.flush()
    await db.refresh(assignment)
    return assignment, []


async def get_by_id(
    db: AsyncSession, assignment_id: uuid.UUID
) -> Optional[ExamAssignment]:
    result = await db.execute(
        select(ExamAssignment).where(ExamAssignment.id == assignment_id)
    )
    return result.scalar_one_or_none()


async def update(
    db: AsyncSession,
    assignment: ExamAssignment,
    data: AssignmentUpdate,
) -> tuple[Optional[ExamAssignment], list[ConflictError]]:
    """
    Merge updates onto the current state, re-validate all rules, then persist.
    Returns (assignment, []) on success or (None, errors) on conflict.
    """
    updates = data.model_dump(exclude_unset=True)

    # Build merged candidate state for validation
    merged = SimpleNamespace(
        exam_id=assignment.exam_id,
        room_id=updates.get("room_id", assignment.room_id),
        seats=updates.get("seats", assignment.seats),
        head_invigilator_id=updates.get("head_invigilator_id", assignment.head_invigilator_id),
        invigilator1_id=updates.get("invigilator1_id", assignment.invigilator1_id),
        invigilator2_id=updates.get("invigilator2_id", assignment.invigilator2_id),
    )

    errors = await conflict_engine.validate_assignment(
        merged, db, exclude_assignment_id=assignment.id
    )
    if errors:
        return None, errors

    for field, value in updates.items():
        setattr(assignment, field, value)
    await db.flush()
    await db.refresh(assignment)
    return assignment, []


async def delete(db: AsyncSession, assignment: ExamAssignment) -> None:
    await db.delete(assignment)
    await db.flush()


async def get_conflicts_for_date(
    db: AsyncSession, target_date: date
) -> list[ConflictError]:
    """
    Scan all assignments on a given date and return any conflicts found.
    Results are deduplicated by (type, invigilator_id).
    """
    result = await db.execute(
        select(ExamAssignment)
        .join(ExamAssignment.exam)
        .where(Exam.exam_date == target_date)
    )
    assignments = result.scalars().all()

    seen: set[tuple] = set()
    conflicts: list[ConflictError] = []

    for a in assignments:
        candidate = SimpleNamespace(
            exam_id=a.exam_id,
            room_id=a.room_id,
            seats=a.seats,
            head_invigilator_id=a.head_invigilator_id,
            invigilator1_id=a.invigilator1_id,
            invigilator2_id=a.invigilator2_id,
        )
        errs = await conflict_engine.validate_assignment(
            candidate, db, exclude_assignment_id=a.id
        )
        for err in errs:
            key = (err.type, str(err.invigilator_id))
            if key not in seen:
                seen.add(key)
                conflicts.append(err)

    return conflicts


async def get_available_invigilators_for_date_slot(
    db: AsyncSession,
    target_date: date,
    time_slot: TimeSlot,
) -> list[Invigilator]:
    """
    Return invigilators who are available and not yet assigned on the
    given date + time_slot combination.
    """
    assigned_sq = union(
        select(ExamAssignment.head_invigilator_id.label("inv_id"))
        .join(ExamAssignment.exam)
        .where(Exam.exam_date == target_date, Exam.time_slot == time_slot),
        select(ExamAssignment.invigilator1_id.label("inv_id"))
        .join(ExamAssignment.exam)
        .where(Exam.exam_date == target_date, Exam.time_slot == time_slot),
        select(ExamAssignment.invigilator2_id.label("inv_id"))
        .join(ExamAssignment.exam)
        .where(
            Exam.exam_date == target_date,
            Exam.time_slot == time_slot,
            ExamAssignment.invigilator2_id.is_not(None),
        ),
    ).subquery()

    q = (
        select(Invigilator)
        .where(
            Invigilator.is_deleted.is_(False),
            Invigilator.status == InvigilatorStatus.available,
            Invigilator.id.not_in(select(assigned_sq.c.inv_id)),
        )
        .order_by(Invigilator.name)
    )

    result = await db.execute(q)
    return result.scalars().all()
