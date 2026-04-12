import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TimeSlot(enum.Enum):
    morning = "morning"
    evening = "evening"


class Exam(Base):
    __tablename__ = "exams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    exam_name: Mapped[str] = mapped_column(String(200), nullable=False)
    exam_date: Mapped[date] = mapped_column(Date, nullable=False)
    time_slot: Mapped[TimeSlot] = mapped_column(
        SAEnum(TimeSlot, name="timeslot"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=datetime.utcnow,
    )

    assignments: Mapped[list["ExamAssignment"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_exams_exam_date", "exam_date"),
    )


class ExamAssignment(Base):
    __tablename__ = "exam_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    exam_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False
    )
    room_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=False
    )
    seats: Mapped[int] = mapped_column(Integer, nullable=False)
    head_invigilator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invigilators.id", ondelete="RESTRICT"),
        nullable=False,
    )
    invigilator1_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invigilators.id", ondelete="RESTRICT"),
        nullable=False,
    )
    invigilator2_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invigilators.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=datetime.utcnow,
    )

    exam: Mapped["Exam"] = relationship(back_populates="assignments")

    __table_args__ = (
        UniqueConstraint("exam_id", "room_id", name="uq_exam_assignments_exam_room"),
        CheckConstraint("seats > 0", name="ck_exam_assignments_seats_positive"),
        CheckConstraint(
            "head_invigilator_id != invigilator1_id",
            name="ck_exam_assignments_head_ne_inv1",
        ),
        CheckConstraint(
            "head_invigilator_id != invigilator2_id",
            name="ck_exam_assignments_head_ne_inv2",
        ),
        CheckConstraint(
            "invigilator1_id != invigilator2_id",
            name="ck_exam_assignments_inv1_ne_inv2",
        ),
        Index("ix_exam_assignments_exam_room", "exam_id", "room_id"),
    )
