import uuid
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.invigilator import InvigilatorStatus
from app.models.user import User
from app.schemas.invigilator import (
    InvigilatorCreate,
    InvigilatorListResponse,
    InvigilatorResponse,
    InvigilatorUpdate,
    InvigilatorUpdateResponse,
    InvigilatorWorkloadResponse,
    WorkloadSummaryResponse,
)
from app.services import invigilator_service as svc

router = APIRouter(prefix="/api/invigilators", tags=["invigilators"])


# NOTE: all static sub-paths (/available, /workload-summary) must be declared
# before /{inv_id} so FastAPI does not treat literal strings as UUID params.


@router.get("/workload-summary", response_model=WorkloadSummaryResponse)
async def get_workload_summary(
    month: int = Query(..., ge=1, le=12),
    year: int = Query(..., ge=2000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return assignment counts for all invigilators in the given month/year."""
    return await svc.get_workload_summary(db, month=month, year=year)


@router.get("/available", response_model=List[InvigilatorResponse])
async def list_available(
    target_date: date = Query(..., alias="date"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return invigilators who are available and not yet assigned on *date*."""
    rows = await svc.get_available_for_date(db, target_date)
    return [InvigilatorResponse.model_validate(r) for r in rows]


@router.get("", response_model=InvigilatorListResponse)
async def list_invigilators(
    search: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    status: Optional[InvigilatorStatus] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return await svc.get_list(
        db,
        search=search,
        department=department,
        status=status,
        page=page,
        limit=limit,
    )


@router.post("", response_model=InvigilatorResponse, status_code=status.HTTP_201_CREATED)
async def create_invigilator(
    body: InvigilatorCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    inv = await svc.create(db, body, user_id=current_user.id)
    return InvigilatorResponse.model_validate(inv)


@router.get("/{inv_id}", response_model=InvigilatorResponse)
async def get_invigilator(
    inv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    inv = await svc.get_by_id(db, inv_id)
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invigilator not found")
    return InvigilatorResponse.model_validate(inv)


@router.get("/{inv_id}/workload", response_model=InvigilatorWorkloadResponse)
async def get_invigilator_workload(
    inv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return full workload data for a single invigilator."""
    result = await svc.get_workload(db, inv_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invigilator not found")
    return result


@router.put("/{inv_id}", response_model=InvigilatorUpdateResponse)
async def update_invigilator(
    inv_id: uuid.UUID,
    body: InvigilatorUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    inv = await svc.get_by_id(db, inv_id)
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invigilator not found")
    inv, affected = await svc.update(db, inv, body, user_id=current_user.id)
    return InvigilatorUpdateResponse(
        invigilator=InvigilatorResponse.model_validate(inv),
        affected_assignments=affected,
    )


@router.delete("/{inv_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invigilator(
    inv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    inv = await svc.get_by_id(db, inv_id)
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invigilator not found")
    await svc.soft_delete(db, inv, user_id=current_user.id)
