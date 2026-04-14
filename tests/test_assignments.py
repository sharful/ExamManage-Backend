"""
Integration tests for the /api/assignments endpoints.

Coverage
--------
* POST /api/assignments            — create: success, all 5 conflict rules enforced
* PUT  /api/assignments/{id}       — update: success, conflict re-validated,
                                     edit doesn't self-conflict
* DELETE /api/assignments/{id}     — delete
* GET  /api/assignments/conflicts  — conflict scan for a date
"""

import uuid
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exam import Exam, ExamAssignment, TimeSlot
from app.models.invigilator import Invigilator, InvigilatorStatus
from app.models.room import Room


# ── Helpers ───────────────────────────────────────────────────────────────────


def _assignment_body(
    exam_id,
    room_id,
    seats=10,
    head_id=None,
    inv1_id=None,
    inv2_id=None,
) -> dict:
    body: dict = {
        "exam_id": str(exam_id),
        "room_id": str(room_id),
        "seats": seats,
        "head_invigilator_id": str(head_id),
        "invigilator1_id": str(inv1_id),
    }
    if inv2_id is not None:
        body["invigilator2_id"] = str(inv2_id)
    return body


def _conflict_types(resp) -> set[str]:
    return {e["type"] for e in resp.json()["detail"]}


# ── CREATE — success ──────────────────────────────────────────────────────────


async def test_create_assignment_success(
    client: AsyncClient,
    exam: Exam,
    room: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    resp = await client.post(
        "/api/assignments",
        json=_assignment_body(exam.id, room.id, head_id=invigilator.id, inv1_id=invigilator2.id),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["exam_id"] == str(exam.id)
    assert body["room_id"] == str(room.id)
    assert uuid.UUID(body["id"])


async def test_create_assignment_with_optional_inv2(
    client: AsyncClient,
    exam: Exam,
    room: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
    invigilator3: Invigilator,
):
    resp = await client.post(
        "/api/assignments",
        json=_assignment_body(
            exam.id, room.id,
            head_id=invigilator.id,
            inv1_id=invigilator2.id,
            inv2_id=invigilator3.id,
        ),
    )
    assert resp.status_code == 201
    assert resp.json()["invigilator2_id"] == str(invigilator3.id)


# ── CREATE — Rule 5: minimum staffing ────────────────────────────────────────


async def test_create_missing_head_rejected(
    client: AsyncClient, exam: Exam, room: Room, invigilator2: Invigilator
):
    body = _assignment_body(exam.id, room.id, head_id=uuid.uuid4(), inv1_id=invigilator2.id)
    # Use a non-existent UUID — will trigger INVIGILATOR_NOT_FOUND which is fine,
    # but let's actually omit head by passing a placeholder None → use pydantic
    body["head_invigilator_id"] = None  # type: ignore[assignment]
    resp = await client.post("/api/assignments", json=body)
    assert resp.status_code == 422  # pydantic rejects None for required UUID


# ── CREATE — Rule 1: unavailable invigilator ─────────────────────────────────


async def test_create_unavailable_head_rejected(
    client: AsyncClient,
    exam: Exam,
    room: Room,
    invigilator_unavailable: Invigilator,
    invigilator2: Invigilator,
):
    resp = await client.post(
        "/api/assignments",
        json=_assignment_body(
            exam.id, room.id,
            head_id=invigilator_unavailable.id,
            inv1_id=invigilator2.id,
        ),
    )
    assert resp.status_code == 409
    assert "UNAVAILABLE" in _conflict_types(resp)


async def test_create_unavailable_inv1_rejected(
    client: AsyncClient,
    exam: Exam,
    room: Room,
    invigilator: Invigilator,
    invigilator_unavailable: Invigilator,
):
    resp = await client.post(
        "/api/assignments",
        json=_assignment_body(
            exam.id, room.id,
            head_id=invigilator.id,
            inv1_id=invigilator_unavailable.id,
        ),
    )
    assert resp.status_code == 409
    assert "UNAVAILABLE" in _conflict_types(resp)


# ── CREATE — Rule 2: double-booking ──────────────────────────────────────────


async def test_create_double_booking_rejected(
    client: AsyncClient,
    db: AsyncSession,
    exam: Exam,
    exam2: Exam,
    room: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
    invigilator3: Invigilator,
):
    # First: assign invigilator + invigilator2 to exam in room
    r1 = await client.post(
        "/api/assignments",
        json=_assignment_body(exam.id, room.id, head_id=invigilator.id, inv1_id=invigilator2.id),
    )
    assert r1.status_code == 201

    # Second: try to assign invigilator (already booked) to exam2 in a different room
    room2 = Room(room_number="DB-ROOM-2", max_seats=50)
    db.add(room2)
    await db.flush()

    r2 = await client.post(
        "/api/assignments",
        json=_assignment_body(exam2.id, room2.id, head_id=invigilator.id, inv1_id=invigilator3.id),
    )
    assert r2.status_code == 409
    assert "DOUBLE_BOOKED" in _conflict_types(r2)


# ── CREATE — Rule 3: duplicate in room ───────────────────────────────────────


async def test_create_duplicate_in_room_rejected(
    client: AsyncClient,
    exam: Exam,
    room: Room,
    invigilator: Invigilator,
):
    resp = await client.post(
        "/api/assignments",
        json=_assignment_body(
            exam.id, room.id, head_id=invigilator.id, inv1_id=invigilator.id
        ),
    )
    assert resp.status_code == 409
    assert "DUPLICATE_IN_ROOM" in _conflict_types(resp)


# ── CREATE — Rule 4: over capacity ───────────────────────────────────────────


async def test_create_over_capacity_rejected(
    client: AsyncClient,
    exam: Exam,
    room_small: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    resp = await client.post(
        "/api/assignments",
        json=_assignment_body(
            exam.id,
            room_small.id,
            seats=room_small.max_seats + 1,
            head_id=invigilator.id,
            inv1_id=invigilator2.id,
        ),
    )
    assert resp.status_code == 409
    assert "OVER_CAPACITY" in _conflict_types(resp)


# ── UPDATE — success ──────────────────────────────────────────────────────────


async def test_update_seats(
    client: AsyncClient,
    db: AsyncSession,
    exam: Exam,
    room: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    assignment = ExamAssignment(
        exam_id=exam.id,
        room_id=room.id,
        seats=10,
        head_invigilator_id=invigilator.id,
        invigilator1_id=invigilator2.id,
    )
    db.add(assignment)
    await db.flush()

    resp = await client.put(f"/api/assignments/{assignment.id}", json={"seats": 20})
    assert resp.status_code == 200
    assert resp.json()["seats"] == 20


async def test_update_swap_invigilator(
    client: AsyncClient,
    db: AsyncSession,
    exam: Exam,
    room: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
    invigilator3: Invigilator,
):
    assignment = ExamAssignment(
        exam_id=exam.id,
        room_id=room.id,
        seats=10,
        head_invigilator_id=invigilator.id,
        invigilator1_id=invigilator2.id,
    )
    db.add(assignment)
    await db.flush()

    resp = await client.put(
        f"/api/assignments/{assignment.id}",
        json={"invigilator1_id": str(invigilator3.id)},
    )
    assert resp.status_code == 200
    assert resp.json()["invigilator1_id"] == str(invigilator3.id)


# ── UPDATE — edit doesn't self-conflict ───────────────────────────────────────


async def test_update_same_invigilators_no_self_conflict(
    client: AsyncClient,
    db: AsyncSession,
    exam: Exam,
    room: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    """
    Updating an assignment while keeping the same invigilators should NOT
    trigger a DOUBLE_BOOKED conflict against itself.
    """
    assignment = ExamAssignment(
        exam_id=exam.id,
        room_id=room.id,
        seats=10,
        head_invigilator_id=invigilator.id,
        invigilator1_id=invigilator2.id,
    )
    db.add(assignment)
    await db.flush()

    resp = await client.put(
        f"/api/assignments/{assignment.id}",
        json={
            "seats": 15,
            "head_invigilator_id": str(invigilator.id),
            "invigilator1_id": str(invigilator2.id),
        },
    )
    assert resp.status_code == 200
    assert resp.json()["seats"] == 15


# ── UPDATE — conflict validation ──────────────────────────────────────────────


async def test_update_to_over_capacity_rejected(
    client: AsyncClient,
    db: AsyncSession,
    exam: Exam,
    room_small: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    assignment = ExamAssignment(
        exam_id=exam.id,
        room_id=room_small.id,
        seats=5,
        head_invigilator_id=invigilator.id,
        invigilator1_id=invigilator2.id,
    )
    db.add(assignment)
    await db.flush()

    resp = await client.put(
        f"/api/assignments/{assignment.id}",
        json={"seats": room_small.max_seats + 99},
    )
    assert resp.status_code == 409
    assert "OVER_CAPACITY" in _conflict_types(resp)


async def test_update_to_double_booked_rejected(
    client: AsyncClient,
    db: AsyncSession,
    exam: Exam,
    exam2: Exam,
    room: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
    invigilator3: Invigilator,
):
    """
    Assignment B: uses inv3 and invigilator2.
    Try to update Assignment B to use invigilator (already in Assignment A on same date+slot).
    """
    room2 = Room(room_number="UPD-ROOM-2", max_seats=50)
    db.add(room2)
    await db.flush()

    assignment_a = ExamAssignment(
        exam_id=exam.id,
        room_id=room.id,
        seats=10,
        head_invigilator_id=invigilator.id,
        invigilator1_id=invigilator2.id,
    )
    assignment_b = ExamAssignment(
        exam_id=exam2.id,
        room_id=room2.id,
        seats=10,
        head_invigilator_id=invigilator3.id,
        invigilator1_id=invigilator2.id,
    )
    db.add_all([assignment_a, assignment_b])
    await db.flush()

    # Try to swap inv3 → invigilator in assignment_b (invigilator is already in A)
    resp = await client.put(
        f"/api/assignments/{assignment_b.id}",
        json={"head_invigilator_id": str(invigilator.id)},
    )
    assert resp.status_code == 409
    assert "DOUBLE_BOOKED" in _conflict_types(resp)


# ── UPDATE — 404 ──────────────────────────────────────────────────────────────


async def test_update_nonexistent_assignment_404(client: AsyncClient):
    resp = await client.put(f"/api/assignments/{uuid.uuid4()}", json={"seats": 5})
    assert resp.status_code == 404


# ── DELETE ────────────────────────────────────────────────────────────────────


async def test_delete_assignment(
    client: AsyncClient,
    db: AsyncSession,
    exam: Exam,
    room: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    assignment = ExamAssignment(
        exam_id=exam.id,
        room_id=room.id,
        seats=10,
        head_invigilator_id=invigilator.id,
        invigilator1_id=invigilator2.id,
    )
    db.add(assignment)
    await db.flush()

    resp = await client.delete(f"/api/assignments/{assignment.id}")
    assert resp.status_code == 204


async def test_delete_nonexistent_assignment_404(client: AsyncClient):
    resp = await client.delete(f"/api/assignments/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── Conflict scan ─────────────────────────────────────────────────────────────


async def test_conflict_scan_empty_when_no_conflicts(
    client: AsyncClient,
    db: AsyncSession,
    exam: Exam,
    room: Room,
    invigilator: Invigilator,
    invigilator2: Invigilator,
):
    assignment = ExamAssignment(
        exam_id=exam.id,
        room_id=room.id,
        seats=10,
        head_invigilator_id=invigilator.id,
        invigilator1_id=invigilator2.id,
    )
    db.add(assignment)
    await db.flush()

    resp = await client.get("/api/assignments/conflicts?exam_date=2026-06-15")
    assert resp.status_code == 200
    # A valid, lone assignment has no conflicts
    types = {e["type"] for e in resp.json()}
    assert "DOUBLE_BOOKED" not in types
    assert "OVER_CAPACITY" not in types
