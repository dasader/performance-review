"""국가 비교 보고서 — 대조표 조립 + 큐잉 + 처리.

숫자 대조는 전부 여기(코드)서 만들고 LLM은 그 표를 인용만 한다. stats.py의 원칙
("숫자를 LLM에 맡기면 틀린다")이 비교에서 특히 중요하다 — 비교 보고서는 숫자 대조가
본체이기 때문이다.

reducer.py에 넣지 않은 이유: reducer는 이미 세부기술 reduce·분야 rollup·로드맵 점검
셋을 담고 있고, 비교는 입력 조립(대조표)이 따로 있어 독립 파일이 맞다.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from app.services import _time
from app.clients import gemini_sync
from app.config import settings
from app.models import Analysis, CountryComparison, Subfield
from app.prompts import COMPARE_INSTRUCTION, country_name

logger = logging.getLogger(__name__)




def collect_country_analyses(
    db: Session, subfield_id: int, year: int, countries: list[str]
) -> list[tuple[str, Analysis]]:
    """요청된 국가의 done 분석을 요청 순서대로 돌려준다.

    하나라도 없으면 ValueError(→409). 일부 국가만으로 비교 보고서를 만들면 "그 국가는
    성과가 없다"로 오독되므로 부분 생성을 아예 막는다.
    """
    found = {
        a.country: a
        for a in db.query(Analysis).filter(
            Analysis.subfield_id == subfield_id,
            Analysis.year == year,
            Analysis.country.in_(countries),
            Analysis.status == "done",
        )
        # 본문이 빈 분석(논문 0건)은 없는 것으로 친다 — 합성에 넣어봐야 모델이 근거
        # 없이 채워 넣을 여지만 준다(rollup_field의 빈 보고서 제외와 같은 이유).
        if a.report_md
    }
    missing = [c for c in countries if c not in found]
    if missing:
        raise ValueError(f"{year}년 완성된 분석이 없는 국가: {', '.join(missing)}")
    return [(c, found[c]) for c in countries]


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
        """하위 항목 한 줄. 앞의 · 가 그룹 제목과 계층을 가른다 — 굵기만으로는
        표에서 제목과 항목이 같은 모양으로 읽힌다(사용자 신고)."""
        return f"| · {label} | " + " | ".join(str(fn(s)) for _, s in rows) + " |"

    def group(label: str) -> str:
        """그룹 제목. 모수 표기((수집 기준)·(분석 기준))는 여기 한 번만 붙인다 —
        항목마다 반복하면 표가 시끄럽다."""
        return f"| **{label}** |" + " |" * len(rows)

    def _population(s: dict) -> int:
        return s.get("population_total") or s.get("searched_count", 0)

    body = [
        # 첫 블록에도 제목을 준다 — 없으면 무엇의 묶음인지 알 수 없다(사용자 신고).
        group("모집단과 표본"),
        line("모집단(전체)", lambda s: f"{_population(s):,}"),
        line("수집", lambda s: f"{s.get('searched_count', 0):,}"),
        line("표본율", lambda s: _pct(s.get("searched_count", 0), _population(s))),
        line("분석(abstract 보유)", lambda s: f"{s.get('analyzed_count', 0):,}"),
        line("abstract 미보유", lambda s: f"{s.get('no_abstract_count', 0):,}"),
        # 귀속과 성과유형은 모수가 다르다 — 실측(차세대 메모리반도체 2025): 귀속 합계는
        # 수집(820)과 같고 성과유형 합계는 분석(731)과 같다. 국가 정보는 초록이 없어도
        # 메타데이터에 있지만 성과유형은 추출 결과라서다. 기준을 표에 적지 않으면
        # "단독+주도+참여가 분석 건수와 안 맞는다"로 읽혀 숫자 전체가 불신받는다.
        group("연구 귀속 (수집 기준)"),
        line("단독", lambda s: f"{s.get('attribution', {}).get('단독', 0):,}"),
        line("주도", lambda s: f"{s.get('attribution', {}).get('주도', 0):,}"),
        line("참여", lambda s: f"{s.get('attribution', {}).get('참여', 0):,}"),
        line("주도 미상", lambda s: f"{s.get('attribution', {}).get('주도 미상', 0):,}"),
        line("국제공동 비율", lambda s: f"{round(s.get('intl_collab_ratio', 0) * 100)}%"),
        group("인용"),
        line("중앙값", lambda s: s.get("citations", {}).get("median", 0)),
        line("p90", lambda s: s.get("citations", {}).get("p90", 0)),
    ]

    # 성과유형은 국가마다 키가 달라 합집합을 만들고 없는 곳은 0으로 채운다. 빠뜨리면
    # "그 국가엔 그 유형이 없다"와 "집계에서 누락됐다"가 구별되지 않는다.
    types: list[str] = []
    for _, s in rows:
        for t in s.get("by_achievement_type", {}):
            if t not in types:
                types.append(t)
    if types:
        body.append(group("성과유형 (분석 기준)"))
        for t in sorted(types):
            body.append(
                line(t, lambda s, t=t: s.get("by_achievement_type", {}).get(t, 0))
            )

    return "\n".join([header, sep, *body])


# 1절 제목. 모델이 번호·공백을 조금씩 다르게 쓰므로 느슨하게 잡는다.
_FIRST_SECTION_RE = re.compile(r"^##\s*1\..*$", re.M)


def _with_table(report_md: str, table: str) -> str:
    """코드가 만든 대조표를 1절 제목 바로 뒤에 끼워 넣는다.

    모델에게 베끼게 하지 않는 이유 — 실측으로 두 번 실패했다: 처음엔 형식을 바꿔
    실어 그룹 제목·계층 기호가 사라졌고, 프롬프트를 고치자 이번엔 표를 통째로 빼고
    서술만 했다. 코드가 이미 갖고 있는 것을 모델에게 왕복시킬 이유가 없다.

    제목을 못 찾으면 맨 앞에 붙인다 — 표가 사라지는 경우는 없어야 한다.
    """
    block = f"\n\n{table}\n"
    m = _FIRST_SECTION_RE.search(report_md or "")
    if m is None:
        return f"{table}\n\n{report_md or ''}"
    return report_md[: m.end()] + block + report_md[m.end():]


def compare_instruction(rows: list[tuple[str, dict]]) -> str:
    """성과유형 개수·목록을 세어 COMPARE_INSTRUCTION에 박는다.

    로드맵 전수 점검(count_goal_rows → {goal_count})과 같은 방식이다. 개수를 못박지
    않으면 모델이 대표 몇 개로 뭉갠다 — 실측(차세대 메모리반도체 2025 KR+CN): 대조표에
    성과유형이 9개인데 §3 서술에는 3개(공정·신소자·아키텍처)만 등장했다.

    세는 일을 모델에게 시키지 않는 것도 같은 이유다(stats.py의 원칙).
    """
    types: list[str] = []
    for _, s in rows:
        for t in s.get("by_achievement_type", {}):
            if t not in types:
                types.append(t)
    types.sort()
    return COMPARE_INSTRUCTION.replace("{type_count}", f"{len(types)}개").replace(
        "{type_list}", ", ".join(types) if types else "(집계된 성과유형 없음)"
    )


def enqueue_comparison(
    db: Session, subfield_id: int, year: int, countries: list[str]
) -> CountryComparison:
    """비교 보고서를 pending으로 큐잉한다(실제 LLM 호출은 runner가 한다).

    검증을 여기(큐잉 시점)서 하는 이유는 FieldReport와 같다 — 관리자가 즉시 404/409를
    받게 하고, 큐잉해 놓고 나중에 조용히 failed되는 것을 피한다.

    국가 목록은 정렬해 저장한다. 같은 조합을 다른 순서로 요청해도 같은 행을 재사용하기
    위해서다 — 안 그러면 같은 비교가 순서만 바꿔 여러 행으로 쌓인다.

    재생성이면 기존 report_md는 그대로 두고 status만 pending으로 되돌린다 —
    처리가 끝나기 전까지 이전 보고서를 계속 보여주기 위해서다.
    """
    if db.get(Subfield, subfield_id) is None:
        raise LookupError(f"세부기술 {subfield_id}를 찾을 수 없습니다.")

    codes = sorted({c.strip().upper() for c in countries if c.strip()})
    if len(codes) < 2:
        raise ValueError("비교하려면 국가가 2개 이상이어야 합니다.")

    # 여기서는 검증만 하고 결과는 버린다 — 처리 시점에 다시 읽는다(그 사이 바뀔 수 있다).
    collect_country_analyses(db, subfield_id, year, codes)

    key = ",".join(codes)
    row = (
        db.query(CountryComparison)
        .filter(
            CountryComparison.subfield_id == subfield_id,
            CountryComparison.year == year,
            CountryComparison.countries == key,
        )
        .one_or_none()
    )
    if row is None:
        row = CountryComparison(
            subfield_id=subfield_id, year=year, countries=key, generated_at=_time.utcnow()
        )
        db.add(row)
    row.status = "pending"
    row.error = None
    db.commit()
    db.refresh(row)
    return row


async def process_comparison(db: Session, row: CountryComparison) -> None:
    """pending 비교 보고서 하나를 실제로 생성한다. runner.loop이 호출한다.

    큐잉 이후 분석이 지워졌을 수 있으므로 여기서 다시 검증한다 — 빈 입력으로 LLM을
    부르면 없는 성과를 지어낸다(reduce_subfield의 no_data 가드와 같은 이유).

    입력은 대조표 + 각국 **종합** 보고서다. sections_json(세부 보고서)은 넣지 않는다 —
    5개국이면 약 725KB(약 18만 토큰)가 되고, 2단계에서 확인한 이중 압축을 비교 단계에서
    반복한다(실측: CN 2025 세부 144,730자 vs 종합 4,813자). 대신 그 대가로 생기는
    "논문 많은 국가의 보고서가 더 짧다"를 COMPARE_INSTRUCTION이 금지한다.
    """
    codes = row.countries.split(",")
    pairs = collect_country_analyses(db, row.subfield_id, row.year, codes)
    subfield = db.get(Subfield, row.subfield_id)
    name = subfield.name if subfield else str(row.subfield_id)

    # stats_json은 JSON 컬럼이라 SQLAlchemy가 이미 dict로 준다 — json.loads를 부르면
    # TypeError가 난다(실측: 첫 실행이 여기서 failed).
    stats_rows = [(code, a.stats_json or {}) for code, a in pairs]
    table = build_comparison_table(stats_rows)
    bodies = "\n\n".join(
        f"## {country_name(code)} 보고서\n{a.report_md}" for code, a in pairs
    )
    payload = (
        f"[세부기술: {name} / {row.year}년 / 비교 국가: "
        f"{', '.join(country_name(c) for c in codes)}]\n\n"
        # 표는 근거로 주되 보고서에 다시 그리라고 하지 않는다 — 삽입은 _with_table이 한다.
        f"### 대조표(코드 집계 — 근거로만 쓰세요. 보고서에는 시스템이 넣습니다)\n"
        f"{table}\n\n{bodies}"
    )

    logger.info(
        "[비교] %s %d년 %s — 보고서 %d건 합성", name, row.year, row.countries, len(pairs)
    )
    generated = await gemini_sync.generate(
        compare_instruction(stats_rows), payload, thinking=settings.thinking_reduce
    )
    row.report_md = _with_table(generated, table)
    row.generated_at = _time.utcnow()
    row.source_count = len(pairs)
    row.status = "done"
    row.error = None
    db.commit()
