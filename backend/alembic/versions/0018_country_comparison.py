"""country_comparisons 테이블 — 국가 비교 보고서 캐시(5단계)

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "country_comparisons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subfield_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        # 정렬된 콤마 구분 국가 코드. 유일키에 포함돼 조합마다 별도 행이 된다.
        sa.Column("countries", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="done"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("report_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["subfield_id"], ["subfields.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subfield_id", "year", "countries", name="uq_comparison"),
    )


def downgrade() -> None:
    op.drop_table("country_comparisons")
