"""field_reports 테이블 — 대분류 보고서 캐시

reducer.rollup_field는 구현만 있고 호출부가 없었다. 관리자가 분야·연도를 골라
합성을 실행하면 그 결과를 여기 저장하고, 공개 조회는 이 캐시만 읽는다
(LLM 1콜이라 매 요청 재생성은 비용·지연 양쪽에서 말이 안 된다).

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "field_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("report_md", sa.Text(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["field_id"], ["fields.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("field_id", "year", name="uq_field_report_year"),
    )


def downgrade() -> None:
    op.drop_table("field_reports")
