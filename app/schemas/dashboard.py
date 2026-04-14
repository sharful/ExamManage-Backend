import uuid
from typing import List, Optional

from pydantic import BaseModel


class DashboardConflict(BaseModel):
    type: str
    message: str
    exam_id: Optional[uuid.UUID] = None
    exam_name: Optional[str] = None
    invigilator_id: Optional[uuid.UUID] = None
    details: Optional[dict] = None


class DashboardResponse(BaseModel):
    exams_today: int
    available_invigilators: int
    unavailable_invigilators: int
    rooms_in_use_today: int
    rooms_free_today: int
    conflicts: List[DashboardConflict]
