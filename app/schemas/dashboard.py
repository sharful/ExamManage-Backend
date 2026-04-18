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


class DailySnapshot(BaseModel):
    date: str                       # ISO date "YYYY-MM-DD"
    exams: int
    invigilators_assigned: int      # distinct invigilators working that day
    rooms_in_use: int
    conflicts: int                  # double-booked invigilator count (heuristic)


class DashboardTrends(BaseModel):
    exams_delta: int                # today - yesterday
    rooms_delta: int                # today - yesterday
    conflicts_delta: int            # today - yesterday
    invigilators_working_today: int # distinct invigilators assigned today


class DashboardResponse(BaseModel):
    exams_today: int
    available_invigilators: int
    unavailable_invigilators: int
    rooms_in_use_today: int
    rooms_free_today: int
    conflicts: List[DashboardConflict]
    history: List[DailySnapshot]    # 7 entries, oldest → today
    trends: DashboardTrends
    exams_next_7_days: int
