"""add analyses.extract_attempts

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'analyses',
        sa.Column('extract_attempts', sa.Integer(), nullable=False, server_default='0'),
    )
    op.alter_column('analyses', 'extract_attempts', server_default=None)


def downgrade() -> None:
    op.drop_column('analyses', 'extract_attempts')
