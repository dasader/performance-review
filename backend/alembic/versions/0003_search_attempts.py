"""add analyses.search_attempts

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'analyses',
        sa.Column('search_attempts', sa.Integer(), nullable=False, server_default='0'),
    )
    op.alter_column('analyses', 'search_attempts', server_default=None)


def downgrade() -> None:
    op.drop_column('analyses', 'search_attempts')
