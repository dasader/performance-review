"""field_reports / roadmap_checks 큐잉 — status·error 컬럼

관리자 "생성"이 즉시 LLM을 부르지 않고 pending 행만 만든 뒤, runner.loop이 한 틱에
하나씩 처리하도록 상태 컬럼을 추가한다. 기존 행은 이미 완성된 캐시이므로 done으로 채운다.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    for table in ("field_reports", "roadmap_checks"):
        # server_default="done": 기존 행은 이미 만들어진 보고서다. 신규 행은 모델
        # default("done")가 아니라 애플리케이션이 pending을 명시한다.
        op.add_column(
            table,
            sa.Column("status", sa.String(length=20), nullable=False, server_default="done"),
        )
        op.add_column(table, sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    for table in ("field_reports", "roadmap_checks"):
        op.drop_column(table, "error")
        op.drop_column(table, "status")
