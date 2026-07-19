"""add paper_extractions.approach, paper_extractions.improvement

map 추출 스키마에 approach(접근 방법)·improvement(기존 대비 개선점) 필드를
추가하면서 함께 도는 컬럼 추가. 기존 행은 이 두 컬럼이 NULL로 남는다 —
mapper.model_ver()에 스키마 버전을 넣어 이런 구행은 자동으로 재추출 대상이
되므로(EXTRACTION_SCHEMA_VERSION), 여기서 백필하지 않는다.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-19 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('paper_extractions', sa.Column('approach', sa.Text(), nullable=True))
    op.add_column('paper_extractions', sa.Column('improvement', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('paper_extractions', 'improvement')
    op.drop_column('paper_extractions', 'approach')
