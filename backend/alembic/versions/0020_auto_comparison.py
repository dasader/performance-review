"""schedule_settings.auto_comparison — 국가 비교 자동 생성 스위치

기본값 false다. 켜기 전에는 현행과 동작이 같아야 하고, 비교는 세부기술·연도당
LLM 여러 콜이라 관리자가 명시적으로 켜야 하는 종류의 비용이다.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "schedule_settings",
        sa.Column("auto_comparison", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("schedule_settings", "auto_comparison")
