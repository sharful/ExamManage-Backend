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
import sqlalchemy as sa

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    # Some environments already have these indexes from schema drift during
    # the refactor rollout, while Alembic is still stamped at the previous
    # revision. Guard the creates so startup can converge instead of failing.
    if not _index_exists('exam_assignments', 'ix_exam_assignments_head_invigilator_id'):
        op.create_index(
            'ix_exam_assignments_head_invigilator_id',
            'exam_assignments',
            ['head_invigilator_id'],
            unique=False,
        )
    if not _index_exists('exam_assignments', 'ix_exam_assignments_invigilator1_id'):
        op.create_index(
            'ix_exam_assignments_invigilator1_id',
            'exam_assignments',
            ['invigilator1_id'],
            unique=False,
        )
    if not _index_exists('exam_assignments', 'ix_exam_assignments_invigilator2_id'):
        op.create_index(
            'ix_exam_assignments_invigilator2_id',
            'exam_assignments',
            ['invigilator2_id'],
            unique=False,
        )
    if not _index_exists('exams', 'ix_exams_date_slot'):
        op.create_index(
            'ix_exams_date_slot',
            'exams',
            ['exam_date', 'time_slot'],
            unique=False,
        )


def downgrade() -> None:
    if _index_exists('exams', 'ix_exams_date_slot'):
        op.drop_index('ix_exams_date_slot', table_name='exams')
    if _index_exists('exam_assignments', 'ix_exam_assignments_invigilator2_id'):
        op.drop_index('ix_exam_assignments_invigilator2_id', table_name='exam_assignments')
    if _index_exists('exam_assignments', 'ix_exam_assignments_invigilator1_id'):
        op.drop_index('ix_exam_assignments_invigilator1_id', table_name='exam_assignments')
    if _index_exists('exam_assignments', 'ix_exam_assignments_head_invigilator_id'):
        op.drop_index('ix_exam_assignments_head_invigilator_id', table_name='exam_assignments')
