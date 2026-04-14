import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.exam import (
    AssignmentResponse,
    ExamCreate,
    ExamDetailResponse,
    ExamListResponse,
    ExamResponse,
    ExamUpdate,
)
from app.services import exam_service as svc

router = APIRouter(prefix="/api/exams", tags=["exams"])


@router.get("", response_model=ExamListResponse)
async def list_exams(
    name: Optional[str] = Query(None),
    exam_date: Optional[date] = Query(None),
    room: Optional[uuid.UUID] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return await svc.get_list(
        db, name=name, exam_date=exam_date, room_id=room, page=page, limit=limit
    )


@router.post("", response_model=ExamResponse, status_code=status.HTTP_201_CREATED)
async def create_exam(
    body: ExamCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    exam = await svc.create(db, body)
    return ExamResponse.model_validate(exam)


@router.get("/{exam_id}", response_model=ExamDetailResponse)
async def get_exam(
    exam_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    exam = await svc.get_by_id(db, exam_id)
    if exam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")
    return ExamDetailResponse(
        **ExamResponse.model_validate(exam).model_dump(),
        assignments=[AssignmentResponse.model_validate(a) for a in exam.assignments],
    )


@router.put("/{exam_id}", response_model=ExamResponse)
async def update_exam(
    exam_id: uuid.UUID,
    body: ExamUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    exam = await svc.get_by_id(db, exam_id)
    if exam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")
    exam = await svc.update(db, exam, body)
    return ExamResponse.model_validate(exam)


@router.delete("/{exam_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_exam(
    exam_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    exam = await svc.get_by_id(db, exam_id)
    if exam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")
    await svc.delete(db, exam)
