"""add_designation_to_invigilators

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'invigilators',
        sa.Column('designation', sa.String(length=100), nullable=True),
    )
    op.execute(
        "UPDATE invigilators SET designation = 'Asst. Professor' "
        "WHERE designation IS NULL"
    )


def downgrade() -> None:
    op.drop_column('invigilators', 'designation')
