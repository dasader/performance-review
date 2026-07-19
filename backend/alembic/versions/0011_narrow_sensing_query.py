"""차세대 고성능 센싱 검색식을 응용환경 축으로 좁힌다

0010의 검색식은 두 번째 절이 `(sensing OR detection OR device)`라 사실상 필터 역할을
못 해, 일반 센서 논문이 대량으로 섞였다(실측 1,051건 — 55개 중 세 번째로 넓었다).
개정안 정의가 "스마트기기, 첨단모빌리티, 극한 환경 등에 특화"를 명시하므로 두 번째
절을 응용환경으로 바꿔 259건까지 좁혔다.

대안으로 소자·부품 축(`"sensor array" OR "readout circuit" OR ...`)도 실측했으나
47~64건으로 과하게 좁아 채택하지 않았다.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None

SUBFIELD_NAME = "차세대 고성능 센싱"
NEW_QUERY = (
    '("image sensor" OR "MEMS sensor" OR "gas sensor" OR "flexible sensor" '
    'OR "tactile sensor" OR LiDAR OR "infrared sensor") AND (automotive '
    'OR "wearable device" OR "harsh environment" OR "high temperature" '
    'OR "edge computing" OR robotics)'
)


def upgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE subfields SET query = :q WHERE name = :n"),
        {"q": NEW_QUERY, "n": SUBFIELD_NAME},
    )


def downgrade() -> None:
    raise NotImplementedError("데이터 교체 마이그레이션이라 되돌릴 수 없다.")
