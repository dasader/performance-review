"""추출 캐시 키에서 subfield_id 제거 — 같은 논문을 세부기술마다 다시 추출하지 않는다

추출 프롬프트(prompts.MAP_INSTRUCTION + map_user_text(title, abstract))에는 세부기술이
전혀 들어가지 않는다. 그런데 캐시 키가 (paper_key, subfield_id, model_ver)이라, 분야가
겹치는 논문은 세부기술 수만큼 같은 입력으로 다시 추출되고 그때마다 과금됐다.

실측(2026-08-25, 이 마이그레이션 직전): 206,744행 중 22,718행(11.0%)이 그 중복이다.
중복 행끼리 텍스트가 달랐던 것은 LLM 샘플링이 비결정적이기 때문이지 세부기술을 반영해서가
아니다(동일 요약 0.1%).

**남길 행은 수치(metrics_json)가 있는 쪽을 먼저 고른다.** 같은 초록을 읽고도 한쪽은 수치를
뽑고 다른 쪽은 못 뽑은 그룹이 493개 있는데(실측), 여기서 무작정 min(id)를 남기면 절반은
수치를 버린다. 수치는 stats.aggregate_metrics가 집계하는 값이라 그대로 화면 숫자가 된다.
나머지(양쪽 다 있거나 양쪽 다 없는 19,411그룹)는 어느 쪽을 남겨도 등가이므로 min(id)로
결정론적으로 고른다.

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
    # 새 유니크 제약을 걸기 전에 중복을 없앤다. 정렬 기준이 곧 "무엇을 남길지"다:
    # ① 수치가 있는 행 우선(위 docstring — 493그룹에서 정보를 지킨다)
    # ② 동률이면 가장 오래된 행(id) — 등가이므로 결정론적이기만 하면 된다.
    # metrics_json은 전 행이 json array임을 확인했으므로 json_array_length가 안전하다.
    op.execute(
        """
        DELETE FROM paper_extractions
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY paper_key, model_ver
                    ORDER BY (json_array_length(metrics_json) > 0) DESC, id
                ) AS rn
                FROM paper_extractions
            ) ranked
            WHERE rn > 1
        )
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
