"""add visits table

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'visits',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('usage_date', sa.Date(), nullable=False),
        sa.Column('visitor_hash', sa.String(length=64), nullable=False),
        sa.UniqueConstraint('usage_date', 'visitor_hash', name='uq_visit_day_hash'),
    )
    op.create_index('ix_visits_usage_date', 'visits', ['usage_date'])


def downgrade() -> None:
    op.drop_index('ix_visits_usage_date', table_name='visits')
    op.drop_table('visits')
