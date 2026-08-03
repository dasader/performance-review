"""국가 비교 보고서 — 대조표 조립 + 큐잉 + 처리.

숫자 대조는 전부 여기(코드)서 만들고 LLM은 그 표를 인용만 한다. stats.py의 원칙
("숫자를 LLM에 맡기면 틀린다")이 비교에서 특히 중요하다 — 비교 보고서는 숫자 대조가
본체이기 때문이다.

reducer.py에 넣지 않은 이유: reducer는 이미 세부기술 reduce·분야 rollup·로드맵 점검
셋을 담고 있고, 비교는 입력 조립(대조표)이 따로 있어 독립 파일이 맞다.
"""

from __future__ import annotations

import logging

from app.prompts import country_name

logger = logging.getLogger(__name__)


def _pct(part: int, whole: int) -> str:
    """모집단이 0인 국가가 섞일 수 있어 0 나눗셈을 막는다."""
    if not whole:
        return "—"
    return f"{round(part / whole * 100)}%"


def build_comparison_table(rows: list[tuple[str, dict]]) -> str:
    """국가별 stats_json에서 대조표(마크다운)를 만든다.

    LLM은 이 표를 그대로 인용만 하고 다시 계산하지 않는다. 표본율 행이 특히 중요하다 —
    이것이 없으면 프롬프트가 "인용수를 비교하지 말라"를 판단할 근거를 잃는다.
    """
    names = [country_name(code) for code, _ in rows]
    header = "| 항목 | " + " | ".join(names) + " |"
    sep = "|---|" + "---:|" * len(rows)

    def line(label: str, fn) -> str:
        return f"| {label} | " + " | ".join(str(fn(s)) for _, s in rows) + " |"

    def _population(s: dict) -> int:
        return s.get("population_total") or s.get("searched_count", 0)

    body = [
        line("모집단(전체)", lambda s: f"{_population(s):,}"),
        line("수집", lambda s: f"{s.get('searched_count', 0):,}"),
        line("표본율", lambda s: _pct(s.get("searched_count", 0), _population(s))),
        line("분석(abstract 보유)", lambda s: f"{s.get('analyzed_count', 0):,}"),
        line("abstract 미보유", lambda s: f"{s.get('no_abstract_count', 0):,}"),
        # 귀속과 성과유형은 모수가 다르다 — 실측(차세대 메모리반도체 2025): 귀속 합계는
        # 수집(820)과 같고 성과유형 합계는 분석(731)과 같다. 국가 정보는 초록이 없어도
        # 메타데이터에 있지만 성과유형은 추출 결과라서다. 기준을 표에 적지 않으면
        # "단독+주도+참여가 분석 건수와 안 맞는다"로 읽혀 숫자 전체가 불신받는다.
        "| **귀속(수집 기준)** |" + " |" * len(rows),
        line("단독", lambda s: f"{s.get('attribution', {}).get('단독', 0):,}"),
        line("주도", lambda s: f"{s.get('attribution', {}).get('주도', 0):,}"),
        line("참여", lambda s: f"{s.get('attribution', {}).get('참여', 0):,}"),
        line("주도 미상", lambda s: f"{s.get('attribution', {}).get('주도 미상', 0):,}"),
        line("국제공동 비율", lambda s: f"{round(s.get('intl_collab_ratio', 0) * 100)}%"),
        line("인용 중앙값", lambda s: s.get("citations", {}).get("median", 0)),
        line("인용 p90", lambda s: s.get("citations", {}).get("p90", 0)),
    ]

    # 성과유형은 국가마다 키가 달라 합집합을 만들고 없는 곳은 0으로 채운다. 빠뜨리면
    # "그 국가엔 그 유형이 없다"와 "집계에서 누락됐다"가 구별되지 않는다.
    types: list[str] = []
    for _, s in rows:
        for t in s.get("by_achievement_type", {}):
            if t not in types:
                types.append(t)
    if types:
        body.append("| **성과유형(분석 기준)** |" + " |" * len(rows))
        for t in sorted(types):
            body.append(
                f"| {t} | "
                + " | ".join(
                    str(s.get("by_achievement_type", {}).get(t, 0)) for _, s in rows
                )
                + " |"
            )

    return "\n".join([header, sep, *body])
