import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, field_validator


class RoomCreate(BaseModel):
    room_number: str
    max_seats: int

    @field_validator("room_number")
    @classmethod
    def room_number_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("room_number must not be blank")
        return v

    @field_validator("max_seats")
    @classmethod
    def max_seats_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_seats must be greater than 0")
        return v


class RoomUpdate(BaseModel):
    room_number: Optional[str] = None
    max_seats: Optional[int] = None

    @field_validator("room_number")
    @classmethod
    def room_number_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("room_number must not be blank")
        return v

    @field_validator("max_seats")
    @classmethod
    def max_seats_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v <= 0:
            raise ValueError("max_seats must be greater than 0")
        return v


class RoomResponse(BaseModel):
    id: uuid.UUID
    room_number: str
    max_seats: int
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginationMeta(BaseModel):
    page: int
    limit: int
    total: int
    pages: int


class RoomListResponse(BaseModel):
    data: List[RoomResponse]
    meta: PaginationMeta
