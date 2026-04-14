import re
import uuid
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, field_validator

from app.models.invigilator import InvigilatorStatus


# ── Availability warning ───────────────────────────────────────────────────────

class AffectedAssignmentInfo(BaseModel):
    assignment_id: uuid.UUID
    exam_id: uuid.UUID
    exam_name: str
    exam_date: date
    room_id: uuid.UUID
    room_number: str
    role: str  # "head" | "invigilator1" | "invigilator2"


class InvigilatorUpdateResponse(BaseModel):
    invigilator: "InvigilatorResponse"
    affected_assignments: List[AffectedAssignmentInfo] = []

_MOBILE_RE = re.compile(r"^\+?[1-9]\d{6,14}$")


class InvigilatorCreate(BaseModel):
    name: str
    department: Optional[str] = None
    institute: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[EmailStr] = None
    status: InvigilatorStatus = InvigilatorStatus.available
    remarks: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not _MOBILE_RE.match(v):
            raise ValueError(
                "mobile must be 7–15 digits, optionally prefixed with +"
            )
        return v


class InvigilatorUpdate(BaseModel):
    name: Optional[str] = None
    department: Optional[str] = None
    institute: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[EmailStr] = None
    status: Optional[InvigilatorStatus] = None
    remarks: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not _MOBILE_RE.match(v):
            raise ValueError(
                "mobile must be 7–15 digits, optionally prefixed with +"
            )
        return v


class InvigilatorResponse(BaseModel):
    id: uuid.UUID
    name: str
    department: Optional[str]
    institute: Optional[str]
    mobile: Optional[str]
    email: Optional[str]
    status: InvigilatorStatus
    remarks: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PaginationMeta(BaseModel):
    page: int
    limit: int
    total: int
    pages: int


class InvigilatorListResponse(BaseModel):
    data: List[InvigilatorResponse]
    meta: PaginationMeta


# ── Workload ──────────────────────────────────────────────────────────────────

class WorkloadMonthEntry(BaseModel):
    year: int
    month: int
    count: int


class InvigilatorWorkloadResponse(BaseModel):
    invigilator_id: uuid.UUID
    name: str
    total_assignments: int
    assignments_by_month: List[WorkloadMonthEntry]
    assigned_dates: List[date]


class WorkloadSummaryItem(BaseModel):
    invigilator_id: uuid.UUID
    name: str
    assignment_count: int


class WorkloadSummaryResponse(BaseModel):
    month: int
    year: int
    data: List[WorkloadSummaryItem]
