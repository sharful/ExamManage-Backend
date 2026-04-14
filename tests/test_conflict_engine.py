"""
Tests for conflict_engine.validate_assignment — all 5 conflict rules.

Rules under test
----------------
1. Availability check   — invigilator must have status=available
2. Double-booking       — same invigilator cannot appear in >1 room on the same date+slot
3. Uniqueness in room   — head / inv1 / inv2 must all be distinct UUIDs
4. Capacity check       — seats <= room.max_seats
5. Minimum staffing     — head_invigilator_id AND invigilator1_id are required

Additional scenarios
--------------------
* Editing an assignment does NOT conflict with itself (exclude_assignment_id)
* Without exclude_assignment_id the same assignment DOES self-conflict
* INVALID_EXAM returned for unknown exam_id
* INVIGILATOR_NOT_FOUND returned for unknown invigilator_id
* Combination: multiple errors returned in one call
* Valid assignment produces zero errors
"""

import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from app.models.exam import Exam, ExamAssignment, TimeSlot
from app.models.invigilator import Invigilator, InvigilatorStatus
from app.models.room import Room
from app.services.conflict_engine import validate_assignment
from sqlalchemy.ext.asyncio import AsyncSession


# ── Helper ────────────────────────────────────────────────────────────────────


def _candidate(
    exam_id,
    room_id,
    seats,
    head_id,
    inv1_id,
    inv2_id=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        exam_id=exam_id,
        room_id=room_id,
        seats=seats,
        head_invigilator_id=head_id,
        invigilator1_id=inv1_id,
        invigilator2_id=inv2_id,
    )


def _types(errors) -> set[str]:
    return {e.type for e in errors}


# ── Rule 5: Minimum staffing ──────────────────────────────────────────────────


async def test_missing_both_required(db: AsyncSession, room: Room, exam: Exam):
    candidate = _candidate(exam.id, room.id, 1, None, None)
    errors = await validate_assignment(candidate, db)
    assert "MISSING_REQUIRED" in _types(errors)


async def test_missing_head_only(
    db: AsyncSession, room: Room, exam: Exam, invigilator: Invigilator
):
    candidate = _candidate(exam.id, room.id, 1, None, invigilator.id)
    errors = await validate_assignment(candidate, db)
    assert "MISSING_REQUIRED" in _types(errors)


async def test_missing_inv1_only(
    db: AsyncSession, room: Room, exam: Exam, invigilator: Invigilator
):
    candidate = _candidate(exam.id, room.id, 1, invigilator.id, None)
    errors = await validate_assignment(candidate, db)
    assert "MISSING_REQUIRED" in _types(errors)


async def test_missing_required_returns_early(db: AsyncSession, room: Room, exam: Exam):
    """MISSING_REQUIRED short-circuits — no other errors added."""
    candidate = _candidate(exam.id, room.id, 1, None, None)
    errors = await validate_assignment(candidate, db)
    assert errors and all(e.type == "MISSING_REQUIRED" for e in errors)


# ── Rule 3: Uniqueness within room ────────────────────────────────────────────


async def test_head_equals_inv1(
    db: AsyncSession, room: Room, exam: Exam, invigilator: Invigilator
):
    candidate = _candidate(exam.id, room.id, 1, invigilator.id, invigilator.id)
    errors = await validate_assignment(candidate, db)
    assert "DUPLICATE_IN_ROOM" in _types(errors)


async def test_inv1_equals_inv2(
    db: AsyncSession,
    room: Room,
    exam: Exam,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    candidate = _candidate(
        exam.id, room.id, 1, invigilator.id, invigilator2.id, inv2_id=invigilator2.id
    )
    errors = await validate_assignment(candidate, db)
    assert "DUPLICATE_IN_ROOM" in _types(errors)


async def test_head_equals_inv2(
    db: AsyncSession,
    room: Room,
    exam: Exam,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    candidate = _candidate(
        exam.id, room.id, 1, invigilator.id, invigilator2.id, inv2_id=invigilator.id
    )
    errors = await validate_assignment(candidate, db)
    assert "DUPLICATE_IN_ROOM" in _types(errors)


# ── Rule 1: Availability check ────────────────────────────────────────────────


async def test_unavailable_head(
    db: AsyncSession,
    room: Room,
    exam: Exam,
    invigilator_unavailable: Invigilator,
    invigilator2: Invigilator,
):
    candidate = _candidate(
        exam.id, room.id, 1, invigilator_unavailable.id, invigilator2.id
    )
    errors = await validate_assignment(candidate, db)
    unavail = [e for e in errors if e.type == "UNAVAILABLE"]
    assert len(unavail) == 1
    assert unavail[0].invigilator_id == invigilator_unavailable.id


async def test_unavailable_inv1(
    db: AsyncSession,
    room: Room,
    exam: Exam,
    invigilator: Invigilator,
    invigilator_unavailable: Invigilator,
):
    candidate = _candidate(
        exam.id, room.id, 1, invigilator.id, invigilator_unavailable.id
    )
    errors = await validate_assignment(candidate, db)
    assert any(
        e.type == "UNAVAILABLE" and e.invigilator_id == invigilator_unavailable.id
        for e in errors
    )


async def test_available_invigilators_no_unavailable_error(
    db: AsyncSession,
    room: Room,
    exam: Exam,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    candidate = _candidate(exam.id, room.id, 1, invigilator.id, invigilator2.id)
    errors = await validate_assignment(candidate, db)
    assert "UNAVAILABLE" not in _types(errors)


# ── Rule 2: Double-booking ────────────────────────────────────────────────────


async def test_double_booking_blocked(
    db: AsyncSession,
    exam: Exam,
    exam2: Exam,
    room: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
    invigilator3: Invigilator,
):
    """Alice assigned to exam (room) should block her from exam2 (same date+slot)."""
    second_room = Room(room_number="EXTRA-A", max_seats=50)
    db.add(second_room)
    await db.flush()

    existing = ExamAssignment(
        exam_id=exam.id,
        room_id=room.id,
        seats=10,
        head_invigilator_id=invigilator.id,
        invigilator1_id=invigilator2.id,
    )
    db.add(existing)
    await db.flush()

    candidate = _candidate(exam2.id, second_room.id, 10, invigilator.id, invigilator3.id)
    errors = await validate_assignment(candidate, db)
    double = [e for e in errors if e.type == "DOUBLE_BOOKED"]
    assert len(double) == 1
    assert double[0].invigilator_id == invigilator.id


async def test_double_booking_inv2_also_detected(
    db: AsyncSession,
    exam: Exam,
    exam2: Exam,
    room: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
    invigilator3: Invigilator,
):
    """Double-booking via the optional inv2 slot is detected."""
    second_room = Room(room_number="EXTRA-B", max_seats=50)
    db.add(second_room)
    await db.flush()

    existing = ExamAssignment(
        exam_id=exam.id,
        room_id=room.id,
        seats=10,
        head_invigilator_id=invigilator.id,
        invigilator1_id=invigilator2.id,
        invigilator2_id=invigilator3.id,
    )
    db.add(existing)
    await db.flush()

    # invigilator3 is in inv2 slot of exam — should be blocked from exam2
    new_head = Invigilator(name="Eva New", status=InvigilatorStatus.available)
    new_inv1 = Invigilator(name="Frank New", status=InvigilatorStatus.available)
    db.add_all([new_head, new_inv1])
    await db.flush()

    candidate = _candidate(
        exam2.id, second_room.id, 10, new_head.id, new_inv1.id, inv2_id=invigilator3.id
    )
    errors = await validate_assignment(candidate, db)
    assert any(
        e.type == "DOUBLE_BOOKED" and e.invigilator_id == invigilator3.id
        for e in errors
    )


async def test_different_date_no_double_booking(
    db: AsyncSession,
    room: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    """Same invigilator on different dates should NOT be a conflict."""
    exam_a = Exam(exam_name="Morning A", exam_date=date(2026, 7, 1), time_slot=TimeSlot.morning)
    exam_b = Exam(exam_name="Morning B", exam_date=date(2026, 7, 2), time_slot=TimeSlot.morning)
    db.add_all([exam_a, exam_b])
    await db.flush()

    room_b = Room(room_number="DIFF-DATE-ROOM", max_seats=50)
    db.add(room_b)
    await db.flush()

    existing = ExamAssignment(
        exam_id=exam_a.id,
        room_id=room.id,
        seats=10,
        head_invigilator_id=invigilator.id,
        invigilator1_id=invigilator2.id,
    )
    db.add(existing)
    await db.flush()

    candidate = _candidate(exam_b.id, room_b.id, 10, invigilator.id, invigilator2.id)
    errors = await validate_assignment(candidate, db)
    assert "DOUBLE_BOOKED" not in _types(errors)


async def test_different_slot_no_double_booking(
    db: AsyncSession,
    room: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    """Same invigilator on same date but different slot should NOT conflict."""
    exam_m = Exam(exam_name="Morning", exam_date=date(2026, 8, 1), time_slot=TimeSlot.morning)
    exam_e = Exam(exam_name="Evening", exam_date=date(2026, 8, 1), time_slot=TimeSlot.evening)
    db.add_all([exam_m, exam_e])
    await db.flush()

    room2 = Room(room_number="DIFF-SLOT-ROOM", max_seats=50)
    db.add(room2)
    await db.flush()

    existing = ExamAssignment(
        exam_id=exam_m.id,
        room_id=room.id,
        seats=10,
        head_invigilator_id=invigilator.id,
        invigilator1_id=invigilator2.id,
    )
    db.add(existing)
    await db.flush()

    candidate = _candidate(exam_e.id, room2.id, 10, invigilator.id, invigilator2.id)
    errors = await validate_assignment(candidate, db)
    assert "DOUBLE_BOOKED" not in _types(errors)


# ── Rule 4: Capacity check ────────────────────────────────────────────────────


async def test_over_capacity_blocked(
    db: AsyncSession,
    room_small: Room,
    exam: Exam,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    candidate = _candidate(
        exam.id, room_small.id, room_small.max_seats + 1, invigilator.id, invigilator2.id
    )
    errors = await validate_assignment(candidate, db)
    assert "OVER_CAPACITY" in _types(errors)


async def test_exact_capacity_allowed(
    db: AsyncSession,
    room_small: Room,
    exam: Exam,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    candidate = _candidate(
        exam.id, room_small.id, room_small.max_seats, invigilator.id, invigilator2.id
    )
    errors = await validate_assignment(candidate, db)
    assert "OVER_CAPACITY" not in _types(errors)


async def test_under_capacity_allowed(
    db: AsyncSession,
    room_small: Room,
    exam: Exam,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    candidate = _candidate(
        exam.id, room_small.id, room_small.max_seats - 1, invigilator.id, invigilator2.id
    )
    errors = await validate_assignment(candidate, db)
    assert "OVER_CAPACITY" not in _types(errors)


# ── Edit doesn't self-conflict ────────────────────────────────────────────────


async def test_edit_excluded_assignment_not_double_booked(
    db: AsyncSession,
    room: Room,
    exam: Exam,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    """
    When editing an assignment with the same invigilators, passing
    exclude_assignment_id suppresses the self-conflict DOUBLE_BOOKED error.
    """
    existing = ExamAssignment(
        exam_id=exam.id,
        room_id=room.id,
        seats=10,
        head_invigilator_id=invigilator.id,
        invigilator1_id=invigilator2.id,
    )
    db.add(existing)
    await db.flush()

    candidate = _candidate(exam.id, room.id, 10, invigilator.id, invigilator2.id)
    errors = await validate_assignment(
        candidate, db, exclude_assignment_id=existing.id
    )
    assert "DOUBLE_BOOKED" not in _types(errors)


async def test_edit_without_exclude_self_conflicts(
    db: AsyncSession,
    room: Room,
    exam: Exam,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    """
    Without exclude_assignment_id the existing assignment IS treated as a
    conflicting booking.
    """
    existing = ExamAssignment(
        exam_id=exam.id,
        room_id=room.id,
        seats=10,
        head_invigilator_id=invigilator.id,
        invigilator1_id=invigilator2.id,
    )
    db.add(existing)
    await db.flush()

    candidate = _candidate(exam.id, room.id, 10, invigilator.id, invigilator2.id)
    errors = await validate_assignment(candidate, db)  # no exclude_assignment_id
    assert "DOUBLE_BOOKED" in _types(errors)


# ── Other error types ─────────────────────────────────────────────────────────


async def test_invalid_exam_id(
    db: AsyncSession,
    room: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    candidate = _candidate(uuid.uuid4(), room.id, 1, invigilator.id, invigilator2.id)
    errors = await validate_assignment(candidate, db)
    assert "INVALID_EXAM" in _types(errors)


async def test_unknown_invigilator_id(
    db: AsyncSession, room: Room, exam: Exam, invigilator: Invigilator
):
    ghost_id = uuid.uuid4()
    candidate = _candidate(exam.id, room.id, 1, invigilator.id, ghost_id)
    errors = await validate_assignment(candidate, db)
    assert any(e.type == "INVIGILATOR_NOT_FOUND" and e.invigilator_id == ghost_id for e in errors)


async def test_room_not_found(
    db: AsyncSession,
    exam: Exam,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    candidate = _candidate(exam.id, uuid.uuid4(), 1, invigilator.id, invigilator2.id)
    errors = await validate_assignment(candidate, db)
    assert "ROOM_NOT_FOUND" in _types(errors)


# ── Edge case combinations ────────────────────────────────────────────────────


async def test_multiple_errors_returned(
    db: AsyncSession,
    room_small: Room,
    exam: Exam,
    invigilator_unavailable: Invigilator,
    invigilator2: Invigilator,
):
    """Unavailable head AND over-capacity should both be reported."""
    candidate = _candidate(
        exam.id,
        room_small.id,
        room_small.max_seats + 99,
        invigilator_unavailable.id,
        invigilator2.id,
    )
    errors = await validate_assignment(candidate, db)
    types = _types(errors)
    assert "UNAVAILABLE" in types
    assert "OVER_CAPACITY" in types


# ── Happy path ────────────────────────────────────────────────────────────────


async def test_valid_assignment_no_errors(
    db: AsyncSession,
    room: Room,
    exam: Exam,
    invigilator: Invigilator,
    invigilator2: Invigilator,
    invigilator3: Invigilator,
):
    candidate = _candidate(
        exam.id, room.id, 10, invigilator.id, invigilator2.id, inv2_id=invigilator3.id
    )
    errors = await validate_assignment(candidate, db)
    assert errors == []


async def test_valid_without_optional_inv2(
    db: AsyncSession,
    room: Room,
    exam: Exam,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    candidate = _candidate(exam.id, room.id, 10, invigilator.id, invigilator2.id)
    errors = await validate_assignment(candidate, db)
    assert errors == []
