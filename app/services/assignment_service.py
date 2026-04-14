import uuid
from datetime import date
from types import SimpleNamespace
from typing import Optional

from sqlalchemy import func, select, union, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.audit import log_action
from app.models.exam import Exam, ExamAssignment, TimeSlot
from app.models.invigilator import Invigilator, InvigilatorStatus
from app.models.room import Room
from app.schemas.exam import AssignmentCreate, AssignmentResponse, AssignmentUpdate, BulkRoomResult, ConflictError
from app.services import conflict_engine

_FIELDS = (
    "exam_id",
    "room_id",
    "seats",
    "head_invigilator_id",
    "invigilator1_id",
    "invigilator2_id",
)


def _snapshot(assignment: ExamAssignment) -> dict:
    return {f: getattr(assignment, f) for f in _FIELDS}


async def create(
    db: AsyncSession,
    data: AssignmentCreate,
    user_id: uuid.UUID | None = None,
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
    await log_action(
        db,
        user_id=user_id,
        action="create",
        entity_type="assignment",
        entity_id=assignment.id,
        old_values=None,
        new_values=_snapshot(assignment),
    )
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
    user_id: uuid.UUID | None = None,
) -> tuple[Optional[ExamAssignment], list[ConflictError]]:
    """
    Merge updates onto the current state, re-validate all rules, then persist.
    Returns (assignment, []) on success or (None, errors) on conflict.
    """
    updates = data.model_dump(exclude_unset=True, exclude={"client_updated_at"})

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

    old_values = {f: getattr(assignment, f) for f in updates}
    for field, value in updates.items():
        setattr(assignment, field, value)
    await db.flush()
    await db.refresh(assignment)
    new_values = {f: getattr(assignment, f) for f in updates}
    await log_action(
        db,
        user_id=user_id,
        action="update",
        entity_type="assignment",
        entity_id=assignment.id,
        old_values=old_values,
        new_values=new_values,
    )
    return assignment, []


async def delete(
    db: AsyncSession,
    assignment: ExamAssignment,
    user_id: uuid.UUID | None = None,
) -> None:
    old_values = _snapshot(assignment)
    entity_id = assignment.id
    await db.delete(assignment)
    await db.flush()
    await log_action(
        db,
        user_id=user_id,
        action="delete",
        entity_type="assignment",
        entity_id=entity_id,
        old_values=old_values,
        new_values=None,
    )


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


async def bulk_auto_assign(
    exam_id: uuid.UUID,
    room_ids: list[uuid.UUID],
    db: AsyncSession,
    user_id: uuid.UUID | None = None,
) -> list[BulkRoomResult]:
    """
    For each room in room_ids, greedily assign invigilators based on:
    1. Availability for the exam date+slot (status=available, not yet assigned)
    2. Workload balance — fewest total assignments first
    3. No conflicts — validated through the conflict engine

    Returns a list of BulkRoomResult (one per requested room).
    """
    # Load exam
    exam_result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = exam_result.scalar_one_or_none()
    if exam is None:
        return [
            BulkRoomResult(room_id=rid, success=False, reason="Exam not found")
            for rid in room_ids
        ]

    # Rooms already assigned for this exam
    existing_result = await db.execute(
        select(ExamAssignment.room_id).where(ExamAssignment.exam_id == exam_id)
    )
    already_assigned_rooms: set[uuid.UUID] = set(existing_result.scalars().all())

    # Load all requested rooms
    rooms_result = await db.execute(
        select(Room).where(Room.id.in_(room_ids))
    )
    room_map: dict[uuid.UUID, Room] = {r.id: r for r in rooms_result.scalars().all()}

    # Available invigilators for this date+slot (not yet assigned anywhere)
    available = await get_available_invigilators_for_date_slot(
        db, exam.exam_date, exam.time_slot
    )

    # Build workload map: total assignment role appearances per invigilator
    head_q = select(ExamAssignment.head_invigilator_id.label("inv_id"))
    inv1_q = select(ExamAssignment.invigilator1_id.label("inv_id"))
    inv2_q = select(ExamAssignment.invigilator2_id.label("inv_id")).where(
        ExamAssignment.invigilator2_id.is_not(None)
    )
    all_roles_sq = union_all(head_q, inv1_q, inv2_q).subquery()
    workload_result = await db.execute(
        select(all_roles_sq.c.inv_id, func.count().label("cnt"))
        .group_by(all_roles_sq.c.inv_id)
    )
    workload_map: dict[uuid.UUID, int] = {row.inv_id: row.cnt for row in workload_result}

    # Sort available pool by ascending workload
    available_sorted = sorted(available, key=lambda inv: workload_map.get(inv.id, 0))

    # Track invigilators committed within this batch (flushed but not yet visible to
    # get_available_invigilators_for_date_slot because the session hasn't been committed)
    committed: set[uuid.UUID] = set()

    results: list[BulkRoomResult] = []

    for room_id in room_ids:
        room = room_map.get(room_id)
        if room is None:
            results.append(BulkRoomResult(room_id=room_id, success=False, reason="Room not found"))
            continue

        if room_id in already_assigned_rooms:
            results.append(
                BulkRoomResult(
                    room_id=room_id,
                    success=False,
                    reason="Room is already assigned for this exam",
                )
            )
            continue

        candidates = [inv for inv in available_sorted if inv.id not in committed]

        if len(candidates) < 2:
            results.append(
                BulkRoomResult(
                    room_id=room_id,
                    success=False,
                    reason=(
                        f"Not enough available invigilators "
                        f"(need at least 2, {len(candidates)} remaining)"
                    ),
                )
            )
            continue

        head = candidates[0]
        inv1 = candidates[1]

        data = AssignmentCreate(
            exam_id=exam_id,
            room_id=room_id,
            seats=room.max_seats,
            head_invigilator_id=head.id,
            invigilator1_id=inv1.id,
            invigilator2_id=None,
        )

        assignment, errors = await create(db, data, user_id=user_id)
        if errors:
            results.append(
                BulkRoomResult(
                    room_id=room_id,
                    success=False,
                    reason="; ".join(e.message for e in errors),
                )
            )
            continue

        committed.add(head.id)
        committed.add(inv1.id)
        already_assigned_rooms.add(room_id)

        results.append(
            BulkRoomResult(
                room_id=room_id,
                success=True,
                assignment=AssignmentResponse.model_validate(assignment),
            )
        )

    return results
