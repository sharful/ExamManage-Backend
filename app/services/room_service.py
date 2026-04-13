import math
import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exam import ExamAssignment
from app.models.room import Room
from app.schemas.room import (
    PaginationMeta,
    RoomCreate,
    RoomListResponse,
    RoomResponse,
    RoomUpdate,
)


async def _get_by_room_number(db: AsyncSession, room_number: str) -> Optional[Room]:
    result = await db.execute(select(Room).where(Room.room_number == room_number))
    return result.scalar_one_or_none()


async def create(db: AsyncSession, data: RoomCreate) -> Room:
    existing = await _get_by_room_number(db, data.room_number)
    if existing is not None:
        raise ValueError(f"Room number '{data.room_number}' already exists")
    room = Room(room_number=data.room_number, max_seats=data.max_seats)
    db.add(room)
    await db.flush()
    await db.refresh(room)
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


async def update(db: AsyncSession, room: Room, data: RoomUpdate) -> Room:
    updates = data.model_dump(exclude_unset=True)

    if "room_number" in updates and updates["room_number"] != room.room_number:
        existing = await _get_by_room_number(db, updates["room_number"])
        if existing is not None:
            raise ValueError(f"Room number '{updates['room_number']}' already exists")

    for field, value in updates.items():
        setattr(room, field, value)
    await db.flush()
    await db.refresh(room)
    return room


async def delete(db: AsyncSession, room: Room) -> None:
    assignment_count: int = (
        await db.execute(
            select(func.count()).where(ExamAssignment.room_id == room.id)
        )
    ).scalar_one()

    if assignment_count > 0:
        raise ValueError(
            f"Cannot delete room: it has {assignment_count} active assignment(s)"
        )

    await db.delete(room)
    await db.flush()
