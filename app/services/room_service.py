import math
import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.audit import log_action
from app.models.exam import Exam, ExamAssignment
from app.models.room import Room
from app.schemas.room import (
    CapacityViolation,
    PaginationMeta,
    RoomCapacityWarning,
    RoomCreate,
    RoomListResponse,
    RoomResponse,
    RoomUpdate,
)


def _snapshot(room: Room) -> dict:
    return {"room_number": room.room_number, "max_seats": room.max_seats}


async def _get_by_room_number(db: AsyncSession, room_number: str) -> Optional[Room]:
    result = await db.execute(select(Room).where(Room.room_number == room_number))
    return result.scalar_one_or_none()


async def create(
    db: AsyncSession,
    data: RoomCreate,
    user_id: uuid.UUID | None = None,
) -> Room:
    existing = await _get_by_room_number(db, data.room_number)
    if existing is not None:
        raise ValueError(f"Room number '{data.room_number}' already exists")
    room = Room(room_number=data.room_number, max_seats=data.max_seats)
    db.add(room)
    await db.flush()
    await db.refresh(room)
    await log_action(
        db,
        user_id=user_id,
        action="create",
        entity_type="room",
        entity_id=room.id,
        old_values=None,
        new_values=_snapshot(room),
    )
    return room


async def get_by_id(db: AsyncSession, room_id: uuid.UUID) -> Optional[Room]:
    result = await db.execute(select(Room).where(Room.id == room_id))
    return result.scalar_one_or_none()


async def get_list(
    db: AsyncSession,
    *,
    page: int = 1,
    limit: int = 20,
) -> RoomListResponse:
    q = select(Room)

    count_q = select(func.count()).select_from(q.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    offset = (page - 1) * limit
    rows = (
        await db.execute(q.order_by(Room.room_number).offset(offset).limit(limit))
    ).scalars().all()

    pages = math.ceil(total / limit) if limit else 1

    return RoomListResponse(
        data=[RoomResponse.model_validate(r) for r in rows],
        meta=PaginationMeta(page=page, limit=limit, total=total, pages=pages),
    )


async def get_capacity_violations(
    db: AsyncSession, room_id: uuid.UUID, new_max_seats: int
) -> list[CapacityViolation]:
    """Return assignments for this room whose seats exceed new_max_seats."""
    rows = (
        await db.execute(
            select(
                ExamAssignment.id,
                ExamAssignment.exam_id,
                ExamAssignment.seats,
                Exam.exam_name,
            )
            .join(Exam, ExamAssignment.exam_id == Exam.id)
            .where(
                ExamAssignment.room_id == room_id,
                ExamAssignment.seats > new_max_seats,
            )
            .order_by(Exam.exam_name)
        )
    ).all()
    return [
        CapacityViolation(
            assignment_id=row.id,
            exam_id=row.exam_id,
            exam_name=row.exam_name,
            seats=row.seats,
        )
        for row in rows
    ]


async def update(
    db: AsyncSession,
    room: Room,
    data: RoomUpdate,
    user_id: uuid.UUID | None = None,
    force: bool = False,
) -> Room:
    updates = data.model_dump(exclude_unset=True)

    if "room_number" in updates and updates["room_number"] != room.room_number:
        existing = await _get_by_room_number(db, updates["room_number"])
        if existing is not None:
            raise ValueError(f"Room number '{updates['room_number']}' already exists")

    # Check if reducing capacity would violate existing assignments
    if "max_seats" in updates and updates["max_seats"] < room.max_seats and not force:
        violations = await get_capacity_violations(db, room.id, updates["max_seats"])
        if violations:
            raise RoomCapacityWarning(
                detail=(
                    f"{len(violations)} assignment(s) currently exceed "
                    f"the new capacity of {updates['max_seats']} seats"
                ),
                violations=violations,
            )

    old_values = {f: getattr(room, f) for f in updates}
    for field, value in updates.items():
        setattr(room, field, value)
    await db.flush()
    await db.refresh(room)
    new_values = {f: getattr(room, f) for f in updates}
    await log_action(
        db,
        user_id=user_id,
        action="update",
        entity_type="room",
        entity_id=room.id,
        old_values=old_values,
        new_values=new_values,
    )
    return room


async def delete(
    db: AsyncSession,
    room: Room,
    user_id: uuid.UUID | None = None,
) -> None:
    assignment_count: int = (
        await db.execute(
            select(func.count()).where(ExamAssignment.room_id == room.id)
        )
    ).scalar_one()

    if assignment_count > 0:
        raise ValueError(
            f"Cannot delete room: it has {assignment_count} active assignment(s)"
        )

    old_values = _snapshot(room)
    entity_id = room.id
    await db.delete(room)
    await db.flush()
    await log_action(
        db,
        user_id=user_id,
        action="delete",
        entity_type="room",
        entity_id=entity_id,
        old_values=old_values,
        new_values=None,
    )
