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
)
from app.services import invigilator_service as svc

router = APIRouter(prefix="/api/invigilators", tags=["invigilators"])


# NOTE: /available must be declared before /{id} so FastAPI does not treat
# the literal string "available" as a UUID path parameter.


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
    _: User = Depends(get_current_user),
):
    inv = await svc.create(db, body)
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


@router.put("/{inv_id}", response_model=InvigilatorResponse)
async def update_invigilator(
    inv_id: uuid.UUID,
    body: InvigilatorUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    inv = await svc.get_by_id(db, inv_id)
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invigilator not found")
    inv = await svc.update(db, inv, body)
    return InvigilatorResponse.model_validate(inv)


@router.delete("/{inv_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invigilator(
    inv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    inv = await svc.get_by_id(db, inv_id)
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invigilator not found")
    await svc.soft_delete(db, inv)
