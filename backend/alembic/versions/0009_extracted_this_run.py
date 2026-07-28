"""add analyses.extracted_this_run

AnalysisRun.new_papers가 "이번 실행의 총 추출 건수 - 이전 총 건수"로 계산되고 있어,
model_ver가 바뀌어 논문 전량이 재추출돼도 총계가 그대로면 0으로 기록되는 버그가 있었다
(실측: analysis 4 — 양자컴퓨팅 2026, 추출 스키마 v1→v2로 논문 10건 전량 재추출됐는데
new_papers=0으로 기록됨).

extracted_this_run: 이번 실행에서 mapper.save_results()가 실제로 LLM 결과를 저장한
논문 수의 누적치(app.services.runner._do_extract). _do_reduce가 done 시점에
AnalysisRun.new_papers로 옮겨 적는다. enqueue()가 분석을 새로 만들거나 되살릴 때 0으로
리셋한다(app.services.runner.enqueue).

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-19 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'analyses',
        sa.Column('extracted_this_run', sa.Integer(), nullable=False, server_default='0'),
    )
    op.alter_column('analyses', 'extracted_this_run', server_default=None)


def downgrade() -> None:
    op.drop_column('analyses', 'extracted_this_run')
