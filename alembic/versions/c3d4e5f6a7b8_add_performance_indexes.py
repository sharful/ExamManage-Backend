"""add_performance_indexes

Adds indexes on exam_assignments invigilator FK columns (head, inv1, inv2)
and a composite index on exams(exam_date, time_slot) to support the
conflict engine's date+slot filter efficiently.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Indexes on exam_assignments invigilator FK columns — used by the
    # conflict engine's double-booking and availability queries.
    op.create_index(
        'ix_exam_assignments_head_invigilator_id',
        'exam_assignments',
        ['head_invigilator_id'],
        unique=False,
    )
    op.create_index(
        'ix_exam_assignments_invigilator1_id',
        'exam_assignments',
        ['invigilator1_id'],
        unique=False,
    )
    op.create_index(
        'ix_exam_assignments_invigilator2_id',
        'exam_assignments',
        ['invigilator2_id'],
        unique=False,
    )

    # Composite index on exams(exam_date, time_slot) — the conflict engine
    # always filters by both columns together when checking double-booking.
    op.create_index(
        'ix_exams_date_slot',
        'exams',
        ['exam_date', 'time_slot'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_exams_date_slot', table_name='exams')
    op.drop_index('ix_exam_assignments_invigilator2_id', table_name='exam_assignments')
    op.drop_index('ix_exam_assignments_invigilator1_id', table_name='exam_assignments')
    op.drop_index('ix_exam_assignments_head_invigilator_id', table_name='exam_assignments')
