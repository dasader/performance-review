"""analyses.sampled 컬럼 제거 — 한 번도 쓰인 적 없는 죽은 컬럼

기획 단계에서 "논문이 너무 많으면 표본만 뽑아 성과를 서술한다"를 염두에 두고 만든
컬럼인데, 그 표본 추출 경로는 구현되지 않았다. 코드 어디에서도 True를 넣지 않으므로
(grep: 정의 1곳, 읽기 1곳, 쓰기 0곳) 이 값에 달린 Report 화면의
"성과 서술은 표본 기준" 안내는 도달할 수 없는 분기였다.

대량 건수는 표본이 아니라 3단 reduce(reducer.group_for_reduce)로 처리하는 쪽으로
정리됐다 — 전수를 성과유형별로 나눠 합성하므로 "표본 기준"이라는 단서 자체가
맞지 않는다. 표본 추출을 실제로 도입한다면 그때 필요한 컬럼을 다시 추가한다.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.drop_column("analyses", "sampled")


def downgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column("sampled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
