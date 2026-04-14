"""
Conflict Detection Engine — validates all 5 rules before an assignment
is created or updated.

Rules (from project_plan.md Section 5):
  1. Availability check      — invigilator status must be 'available'
  2. Single-duty rule        — cannot be in >1 room on the same date+slot
  3. Uniqueness within room  — head / inv1 / inv2 must all be distinct
  4. Capacity check          — seats <= room.max_seats
  5. Minimum staffing        — head_invigilator_id and invigilator1_id required
"""

import uuid
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exam import Exam, ExamAssignment
from app.models.invigilator import Invigilator, InvigilatorStatus
from app.models.room import Room
from app.schemas.exam import ConflictError


async def validate_assignment(
    assignment_data: Any,
    db: AsyncSession,
    *,
    exclude_assignment_id: Optional[uuid.UUID] = None,
) -> list[ConflictError]:
    """
    Validate assignment_data against all conflict rules.

    assignment_data must expose:
        exam_id, room_id, seats,
        head_invigilator_id, invigilator1_id, invigilator2_id (optional)

    exclude_assignment_id: when editing an existing assignment, pass its id
    so we don't flag it as conflicting with itself.

    Returns a (possibly empty) list of ConflictError.
    """
    errors: list[ConflictError] = []

    head_id: Optional[uuid.UUID] = assignment_data.head_invigilator_id
    inv1_id: Optional[uuid.UUID] = assignment_data.invigilator1_id
    inv2_id: Optional[uuid.UUID] = assignment_data.invigilator2_id
    room_id: uuid.UUID = assignment_data.room_id
    seats: int = assignment_data.seats
    exam_id: uuid.UUID = assignment_data.exam_id

    # ── Rule 5: Minimum staffing ─────────────────────────────────────────────
    if head_id is None or inv1_id is None:
        errors.append(ConflictError(
            type="MISSING_REQUIRED",
            message="head_invigilator_id and invigilator1_id are both required",
        ))
        return errors  # cannot continue without required fields

    # ── Rule 3: Uniqueness within room ───────────────────────────────────────
    ids = [i for i in [head_id, inv1_id, inv2_id] if i is not None]
    if len(ids) != len(set(ids)):
        errors.append(ConflictError(
            type="DUPLICATE_IN_ROOM",
            message="head_invigilator_id, invigilator1_id and invigilator2_id must all be distinct",
        ))

    # ── Resolve exam (needed for date+slot checks) ───────────────────────────
    exam_result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = exam_result.scalar_one_or_none()
    if exam is None:
        errors.append(ConflictError(
            type="INVALID_EXAM",
            message=f"Exam {exam_id} not found",
        ))
        return errors

    # ── Fetch each invigilator once ──────────────────────────────────────────
    inv_map: dict[uuid.UUID, Optional[Invigilator]] = {}
    for inv_id in ids:
        if inv_id not in inv_map:
            result = await db.execute(
                select(Invigilator).where(
                    Invigilator.id == inv_id,
                    Invigilator.is_deleted.is_(False),
                )
            )
            inv_map[inv_id] = result.scalar_one_or_none()

    # ── Rule 1: Availability check ───────────────────────────────────────────
    for inv_id in ids:
        inv = inv_map.get(inv_id)
        if inv is None:
            errors.append(ConflictError(
                type="INVIGILATOR_NOT_FOUND",
                message=f"Invigilator {inv_id} not found",
                invigilator_id=inv_id,
            ))
        elif inv.status != InvigilatorStatus.available:
            errors.append(ConflictError(
                type="UNAVAILABLE",
                message=f"Invigilator '{inv.name}' is not available",
                invigilator_id=inv_id,
            ))

    # ── Rule 2: Single-duty rule (double-booking) ────────────────────────────
    for inv_id in ids:
        q = (
            select(ExamAssignment)
            .join(ExamAssignment.exam)
            .where(
                Exam.exam_date == exam.exam_date,
                Exam.time_slot == exam.time_slot,
                or_(
                    ExamAssignment.head_invigilator_id == inv_id,
                    ExamAssignment.invigilator1_id == inv_id,
                    ExamAssignment.invigilator2_id == inv_id,
                ),
            )
        )
        if exclude_assignment_id is not None:
            q = q.where(ExamAssignment.id != exclude_assignment_id)

        result = await db.execute(q)
        existing = result.scalars().all()
        if existing:
            inv = inv_map.get(inv_id)
            inv_name = inv.name if inv else str(inv_id)
            errors.append(ConflictError(
                type="DOUBLE_BOOKED",
                message=(
                    f"Invigilator '{inv_name}' is already assigned to another "
                    "room on the same date and time slot"
                ),
                invigilator_id=inv_id,
                details={"conflicting_assignment_id": str(existing[0].id)},
            ))

    # ── Rule 4: Capacity check ───────────────────────────────────────────────
    room_result = await db.execute(select(Room).where(Room.id == room_id))
    room = room_result.scalar_one_or_none()
    if room is None:
        errors.append(ConflictError(
            type="ROOM_NOT_FOUND",
            message=f"Room {room_id} not found",
        ))
    elif seats > room.max_seats:
        errors.append(ConflictError(
            type="OVER_CAPACITY",
            message=(
                f"Assigned seats ({seats}) exceed room capacity ({room.max_seats})"
            ),
            details={"seats": seats, "max_seats": room.max_seats},
        ))

    return errors
