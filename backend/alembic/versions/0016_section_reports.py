"""analyses.sections_json — 3단 reduce의 그룹별 중간 보고서 보존

현행은 partial을 최종 통합 1콜로 다시 압축하면서 버렸다. 그 이중 압축이 500건 이상에서
인용률이 무너지는 원인이라(실측 9.7% → 5.6%), 버리지 않고 남겨 화면에서 펼쳐볼 수 있게 한다.

기존 행은 빈 리스트로 채운다 — 이미 3단 reduce가 지나가 partial이 남아 있지 않다.
해당 분석을 다시 실행해야 채워진다.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column("sections_json", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("analyses", "sections_json")
