"""국가 파라미터화 — analyses.country, papers.lead_countries_json, schedule_settings.countries

기존 분석은 전부 한국 대상이므로 country='KR'로 채운다 — 재실행이 필요 없다.
분석 유일키에 country를 더해 같은 세부기술·연도를 국가별로 따로 둘 수 있게 한다.

papers.korea_flag는 삭제한다. 전수 grep 결과 쓰기만 하고 읽는 곳이 한 군데도 없었고,
국가가 파라미터가 되면 "한국인가"라는 단일 불리언은 의미가 사라진다(같은 정보는
countries_json에 있다).

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column("country", sa.String(length=2), nullable=False, server_default="KR"),
    )
    op.drop_constraint("uq_analysis_year", "analyses", type_="unique")
    op.create_unique_constraint(
        "uq_analysis_year", "analyses", ["subfield_id", "year", "country"]
    )
    op.add_column(
        "papers",
        sa.Column("lead_countries_json", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.drop_column("papers", "korea_flag")
    op.add_column(
        "schedule_settings",
        sa.Column("countries", sa.String(length=100), nullable=False, server_default="KR"),
    )


def downgrade() -> None:
    op.drop_column("schedule_settings", "countries")
    op.add_column(
        "papers",
        sa.Column("korea_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.drop_column("papers", "lead_countries_json")
    op.drop_constraint("uq_analysis_year", "analyses", type_="unique")
    op.create_unique_constraint("uq_analysis_year", "analyses", ["subfield_id", "year"])
    op.drop_column("analyses", "country")
