"""추출 캐시 키에서 subfield_id 제거 — 같은 논문을 세부기술마다 다시 추출하지 않는다

추출 프롬프트(prompts.MAP_INSTRUCTION + map_user_text(title, abstract))에는 세부기술이
전혀 들어가지 않는다. 그런데 캐시 키가 (paper_key, subfield_id, model_ver)이라, 분야가
겹치는 논문은 세부기술 수만큼 같은 입력으로 다시 추출되고 그때마다 과금됐다.

실측(2026-08-25, 이 마이그레이션 직전): 206,744행 중 22,718행(11.0%)이 그 중복이다.
중복 행끼리 텍스트가 달랐던 것은 LLM 샘플링이 비결정적이기 때문이지 세부기술을 반영해서가
아니다(동일 요약 0.1%) — 즉 어느 쪽을 남겨도 같은 값이라 (paper_key, model_ver)당 가장
오래된 행(min(id))을 남긴다.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # 새 유니크 제약을 걸기 전에 중복을 없앤다. 남길 행은 (paper_key, model_ver)당
    # 가장 작은 id — 위 docstring대로 어느 쪽을 남겨도 등가다.
    op.execute(
        """
        DELETE FROM paper_extractions a
        USING paper_extractions b
        WHERE a.paper_key = b.paper_key
          AND a.model_ver = b.model_ver
          AND a.id > b.id
        """
    )
    op.drop_constraint("uq_extraction", "paper_extractions", type_="unique")
    op.drop_column("paper_extractions", "subfield_id")
    op.create_unique_constraint("uq_extraction", "paper_extractions", ["paper_key", "model_ver"])


def downgrade() -> None:
    """되돌리면 subfield_id 값은 복원되지 않는다.

    어느 세부기술이 이 추출을 처음 일으켰는지는 위 DELETE로 이미 사라졌고, 애초에
    추출 결과와 무관한 값이라 보존해 둔 곳도 없다. 추출은 캐시라 값이 틀리는 것보다
    비어 있는 편이 낫다 — 되돌린 뒤 다시 채우려면 재추출해야 한다.
    그래서 NOT NULL을 즉시 걸 수 없다: 기존 행을 채울 값이 없으므로 nullable로 되돌린다.
    """
    op.drop_constraint("uq_extraction", "paper_extractions", type_="unique")
    op.add_column("paper_extractions", sa.Column("subfield_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "paper_extractions_subfield_id_fkey",
        "paper_extractions", "subfields", ["subfield_id"], ["id"],
    )
    op.create_unique_constraint(
        "uq_extraction", "paper_extractions", ["paper_key", "subfield_id", "model_ver"]
    )
