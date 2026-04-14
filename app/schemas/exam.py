import uuid
from datetime import date, datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, field_validator

from app.models.exam import TimeSlot


# ── Conflict ─────────────────────────────────────────────────────────────────

class ConflictError(BaseModel):
    type: str  # UNAVAILABLE | DOUBLE_BOOKED | DUPLICATE_IN_ROOM | OVER_CAPACITY | MISSING_REQUIRED
    message: str
    invigilator_id: Optional[uuid.UUID] = None
    details: Optional[dict] = None


# ── Assignment ────────────────────────────────────────────────────────────────

class AssignmentCreate(BaseModel):
    exam_id: uuid.UUID
    room_id: uuid.UUID
    seats: int
    head_invigilator_id: uuid.UUID
    invigilator1_id: uuid.UUID
    invigilator2_id: Optional[uuid.UUID] = None

    @field_validator("seats")
    @classmethod
    def seats_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("seats must be greater than 0")
        return v


class AssignmentUpdate(BaseModel):
    room_id: Optional[uuid.UUID] = None
    seats: Optional[int] = None
    head_invigilator_id: Optional[uuid.UUID] = None
    invigilator1_id: Optional[uuid.UUID] = None
    invigilator2_id: Optional[uuid.UUID] = None
    # Optimistic locking: client sends the updated_at it loaded; server rejects if stale
    client_updated_at: Optional[datetime] = None

    @field_validator("seats")
    @classmethod
    def seats_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v <= 0:
            raise ValueError("seats must be greater than 0")
        return v


class AssignmentResponse(BaseModel):
    id: uuid.UUID
    exam_id: uuid.UUID
    room_id: uuid.UUID
    seats: int
    head_invigilator_id: uuid.UUID
    invigilator1_id: uuid.UUID
    invigilator2_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Exam ──────────────────────────────────────────────────────────────────────

class ExamCreate(BaseModel):
    exam_name: str
    exam_date: date
    time_slot: TimeSlot

    @field_validator("exam_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("exam_name must not be blank")
        return v


class ExamUpdate(BaseModel):
    exam_name: Optional[str] = None
    exam_date: Optional[date] = None
    time_slot: Optional[TimeSlot] = None

    @field_validator("exam_name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("exam_name must not be blank")
        return v


class ExamResponse(BaseModel):
    id: uuid.UUID
    exam_name: str
    exam_date: date
    time_slot: TimeSlot
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ExamDetailResponse(ExamResponse):
    assignments: List[AssignmentResponse] = []


class PaginationMeta(BaseModel):
    page: int
    limit: int
    total: int
    pages: int


class ExamListResponse(BaseModel):
    data: List[ExamResponse]
    meta: PaginationMeta


# ── Clone exam ────────────────────────────────────────────────────────────────

class ExamCloneRequest(BaseModel):
    new_exam_name: str
    new_date: date

    @field_validator("new_exam_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("new_exam_name must not be blank")
        return v


class ClonedAssignmentResult(BaseModel):
    assignment: AssignmentResponse
    has_conflicts: bool
    conflicts: List[ConflictError]


class ExamCloneResponse(BaseModel):
    exam: ExamResponse
    assignments: List[ClonedAssignmentResult]
    total_assignments: int
    conflict_count: int


# ── Bulk auto-assign ──────────────────────────────────────────────────────────

class BulkAutoAssignRequest(BaseModel):
    exam_id: uuid.UUID
    room_ids: List[uuid.UUID]


class BulkRoomResult(BaseModel):
    room_id: uuid.UUID
    success: bool
    assignment: Optional[AssignmentResponse] = None
    reason: Optional[str] = None


class BulkAutoAssignResponse(BaseModel):
    results: List[BulkRoomResult]
    assigned_count: int
    failed_count: int
