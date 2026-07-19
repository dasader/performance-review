"""add analyses.trigger/report_model_ver, analysis_runs, scheduled_runs

월간 자동 분석 스케줄러 지원:
- analyses.trigger: 이 행을 마지막으로 활성화한 원인(manual|scheduled).
- analyses.report_model_ver: 마지막 보고서 생성 시점의 model_ver — 추출 건수가 그대로여도
  model_ver가 바뀌어 전량 재추출된 경우를 구분해 보고서 재생성 생략 로직이 오판하지
  않게 한다.
- analysis_runs: 실행마다 검색/분석 건수를 남겨 "월별로 논문이 실제로 얼마나 느는가"를
  나중에 데이터로 확인한다(조회 API는 범위 밖 — 기록만).
- scheduled_runs: run_month(예: "2026-08")를 unique로 두어 같은 달 중복 큐잉을 막는다.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-19 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'analyses',
        sa.Column('trigger', sa.String(length=20), nullable=False, server_default='manual'),
    )
    op.alter_column('analyses', 'trigger', server_default=None)
    op.add_column('analyses', sa.Column('report_model_ver', sa.String(length=80), nullable=True))

    op.create_table(
        'analysis_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('analysis_id', sa.Integer(), sa.ForeignKey('analyses.id'), nullable=False),
        sa.Column('ran_at', sa.DateTime(), nullable=False),
        sa.Column('searched_count', sa.Integer(), nullable=False),
        sa.Column('analyzed_count', sa.Integer(), nullable=False),
        sa.Column('new_papers', sa.Integer(), nullable=False),
        sa.Column('trigger', sa.String(length=20), nullable=False),
    )
    op.create_index('ix_analysis_runs_analysis_id', 'analysis_runs', ['analysis_id'])

    op.create_table(
        'scheduled_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('run_month', sa.String(length=7), nullable=False),
        sa.Column('ran_at', sa.DateTime(), nullable=False),
        sa.Column('queued_count', sa.Integer(), nullable=False, server_default='0'),
        sa.UniqueConstraint('run_month', name='uq_scheduled_run_month'),
    )


def downgrade() -> None:
    op.drop_table('scheduled_runs')
    op.drop_index('ix_analysis_runs_analysis_id', table_name='analysis_runs')
    op.drop_table('analysis_runs')
    op.drop_column('analyses', 'report_model_ver')
    op.drop_column('analyses', 'trigger')
