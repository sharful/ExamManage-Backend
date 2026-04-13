"""add_is_deleted_to_invigilators

Revision ID: a1b2c3d4e5f6
Revises: 9f3d1cc9f387
Create Date: 2026-04-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '9f3d1cc9f387'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'invigilators',
        sa.Column(
            'is_deleted',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )
    op.create_index('ix_invigilators_is_deleted', 'invigilators', ['is_deleted'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_invigilators_is_deleted', table_name='invigilators')
    op.drop_column('invigilators', 'is_deleted')
