"""add schedule_settings table, scheduled_runs.trigger

관리자 화면에서 런타임으로 스케줄을 바꿀 수 있게 한다:
- schedule_settings: enabled/day/hour/years_back의 싱글턴(id=1) 설정 행. .env의
  SCHEDULE_ENABLED/DAY/HOUR/YEARS_BACK은 이제 이 행이 없을 때의 초기 기본값으로만
  쓰인다(app.services.runner.get_schedule_settings). schedule_timezone은 여기 포함하지
  않는다 — .env 전용, 화면에는 읽기 전용으로만 노출한다.
- scheduled_runs.trigger: "scheduled"(정기 실행) | "manual"(관리자 "지금 실행"). 관리자
  화면의 "지금 실행"이 별도 run_month 키("YYYY-MM-manual-...")를 쓰므로 그 달의 정기
  실행 멱등성 키(run_month unique)와 충돌하지 않는다 — run_month 컬럼 길이를
  7 → 40으로 늘린다.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-19 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'schedule_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('day', sa.Integer(), nullable=False),
        sa.Column('hour', sa.Integer(), nullable=False),
        sa.Column('years_back', sa.Integer(), nullable=False),
    )

    op.add_column(
        'scheduled_runs',
        sa.Column('trigger', sa.String(length=20), nullable=False, server_default='scheduled'),
    )
    op.alter_column('scheduled_runs', 'trigger', server_default=None)
    op.alter_column('scheduled_runs', 'run_month', type_=sa.String(length=40))


def downgrade() -> None:
    op.alter_column('scheduled_runs', 'run_month', type_=sa.String(length=7))
    op.drop_column('scheduled_runs', 'trigger')
    op.drop_table('schedule_settings')
