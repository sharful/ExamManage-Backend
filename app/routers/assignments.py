import uuid
from datetime import date
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.exam import TimeSlot
from app.models.user import User
from app.schemas.exam import (
    AssignmentCreate,
    AssignmentResponse,
    AssignmentUpdate,
    BulkAutoAssignRequest,
    BulkAutoAssignResponse,
    BulkRoomResult,
    ConflictError,
)
from app.schemas.invigilator import InvigilatorResponse
from app.services import assignment_service as svc

router = APIRouter(prefix="/api/assignments", tags=["assignments"])


# Static sub-routes MUST come before /{assignment_id} to avoid path collisions.

@router.get("/conflicts", response_model=List[ConflictError])
async def get_conflicts(
    exam_date: date = Query(..., description="Date to scan for conflicts (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return await svc.get_conflicts_for_date(db, exam_date)


@router.get("/available-invigilators", response_model=List[InvigilatorResponse])
async def get_available_invigilators(
    exam_date: date = Query(...),
    time_slot: TimeSlot = Query(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    invigs = await svc.get_available_invigilators_for_date_slot(db, exam_date, time_slot)
    return [InvigilatorResponse.model_validate(i) for i in invigs]


@router.post("", response_model=AssignmentResponse, status_code=status.HTTP_201_CREATED)
async def create_assignment(
    body: AssignmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assignment, errors = await svc.create(db, body, user_id=current_user.id)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=[e.model_dump(mode="json") for e in errors],
        )
    return AssignmentResponse.model_validate(assignment)


@router.post("/bulk", response_model=BulkAutoAssignResponse, status_code=status.HTTP_200_OK)
async def bulk_auto_assign(
    body: BulkAutoAssignRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    results = await svc.bulk_auto_assign(
        exam_id=body.exam_id,
        room_ids=body.room_ids,
        db=db,
        user_id=current_user.id,
    )
    return BulkAutoAssignResponse(
        results=results,
        assigned_count=sum(1 for r in results if r.success),
        failed_count=sum(1 for r in results if not r.success),
    )


@router.put("/{assignment_id}", response_model=AssignmentResponse)
async def update_assignment(
    assignment_id: uuid.UUID,
    body: AssignmentUpdate,
    force: bool = Query(False, description="Skip optimistic-lock check and save anyway"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assignment = await svc.get_by_id(db, assignment_id)
    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found"
        )

    # Optimistic locking: reject if the client's version is stale
    if body.client_updated_at is not None and not force:
        # Normalise both timestamps to UTC-naive for comparison
        db_ts = assignment.updated_at.replace(tzinfo=None) if assignment.updated_at.tzinfo else assignment.updated_at
        client_ts = body.client_updated_at.replace(tzinfo=None) if body.client_updated_at.tzinfo else body.client_updated_at
        if db_ts != client_ts:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=[
                    ConflictError(
                        type="STALE_DATA",
                        message=(
                            "This assignment was modified by someone else since you "
                            "opened it. Reload to see the latest version, or force-save "
                            "to overwrite."
                        ),
                        details={
                            "server_updated_at": assignment.updated_at.isoformat(),
                        },
                    ).model_dump()
                ],
            )

    updated, errors = await svc.update(db, assignment, body, user_id=current_user.id)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=[e.model_dump(mode="json") for e in errors],
        )
    return AssignmentResponse.model_validate(updated)


@router.delete("/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_assignment(
    assignment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assignment = await svc.get_by_id(db, assignment_id)
    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found"
        )
    await svc.delete(db, assignment, user_id=current_user.id)
