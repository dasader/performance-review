"""roadmaps / roadmap_checks — 전략기술로드맵 원문과 이행 점검 보고서

로드맵은 분야당 한 판본만 둔다(unique field_id). 점검 보고서는 FieldReport와
별개 테이블이다 — 로드맵 없는 분야도 종합 보고서는 쓸 수 있어야 하고, 로드맵만
개정됐을 때 점검만 다시 돌릴 수 있어야 한다.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "roadmaps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(length=200), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["field_id"], ["fields.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("field_id"),
    )
    op.create_table(
        "roadmap_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("report_md", sa.Text(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("goal_count", sa.Integer(), nullable=False),
        sa.Column("checked_count", sa.Integer(), nullable=False),
        sa.Column("roadmap_version", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(["field_id"], ["fields.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("field_id", "year", name="uq_roadmap_check_year"),
    )


def downgrade() -> None:
    op.drop_table("roadmap_checks")
    op.drop_table("roadmaps")
