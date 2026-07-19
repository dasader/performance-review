import logging
from collections import defaultdict

from sqlalchemy.orm import Session

from app.clients import gemini_sync
from app.config import settings
from app.models.analysis import Analysis
from app.models.field import Subfield
from app.models.paper import Paper, PaperExtraction
from app.prompts import REDUCE_INSTRUCTION, ROLLUP_INSTRUCTION

logger = logging.getLogger(__name__)


def format_extractions(
    extractions: list[PaperExtraction], papers_by_key: dict[str, Paper]
) -> str:
    """추출 결과를 reduce 입력용 텍스트로. 논문당 한 줄이라 수백 건도 컨텍스트에 들어간다."""
    lines: list[str] = []
    for e in extractions:
        paper = papers_by_key.get(e.paper_key)
        if paper is None:
            continue
        metrics = ", ".join(
            f"{m.get('name')} {m.get('value')}{m.get('unit', '')}" for m in (e.metrics_json or [])
        )
        line = f"- [{paper.year or '연도미상'}] {paper.title} | {e.achievement_type or '기타'} | {e.tech_summary}"
        if e.approach:
            line += f" | 접근: {e.approach}"
        if e.improvement:
            line += f" | 개선점: {e.improvement}"
        if metrics:
            line += f" | 수치: {metrics}"
        lines.append(line)
    return "\n".join(lines)


def group_for_reduce(extractions: list[PaperExtraction]) -> dict[str, list[PaperExtraction]]:
    """임계값 이하면 한 번에 합성하고, 넘으면 성과유형별로 나눠 3단 reduce로 간다."""
    if len(extractions) <= settings.reduce_group_threshold:
        return {"전체": extractions}

    by_type: dict[str, list[PaperExtraction]] = defaultdict(list)
    for e in extractions:
        by_type[e.achievement_type or "기타"].append(e)

    # 한 성과유형에 전부 몰리면 유형 분할만으로는 임계값 아래로 내려가지 않는다.
    # 그런 그룹은 임계값 크기로 다시 쪼갠다.
    size = settings.reduce_group_threshold
    groups: dict[str, list[PaperExtraction]] = {}
    for name, items in by_type.items():
        if len(items) <= size:
            groups[name] = items
            continue
        for i in range(0, len(items), size):
            groups[f"{name} ({i // size + 1})"] = items[i:i + size]

    logger.info("[reduce] %d건 → %d개 그룹으로 분할", len(extractions), len(groups))
    return groups


async def reduce_subfield(
    db: Session,
    analysis: Analysis,
    extractions: list[PaperExtraction],
    papers_by_key: dict[str, Paper],
) -> str:
    """세부기술 보고서 생성. 추출 결과가 0건이거나, 있어도 papers_by_key 매칭 실패로
    LLM에 보낼 본문이 비면 LLM을 호출하지 않는다 —
    빈 입력으로 부르면 모델이 성과를 통째로 지어낸다."""
    no_data_message = "분석 대상 논문이 없어 성과를 정리할 수 없습니다."
    if not extractions:
        return no_data_message

    # 대상 세부기술명을 입력에 명시한다. 없으면 모델이 본문 내용만 보고 H1 제목을 새로
    # 지어내, 목록 화면의 세부기술명과 보고서 제목이 어긋난다(실측: "재생에너지" 분석의
    # 보고서 제목이 "에너지 변환 및 자원 순환 공학"으로 나왔다). 3단 reduce는 최종 합성
    # 입력이 중간 요약뿐이라 세부기술명이 아예 사라져 더 크게 어긋난다.
    subfield = db.get(Subfield, analysis.subfield_id)
    header = f"[세부기술: {subfield.name if subfield else '미상'} / {analysis.year}]\n"

    groups = group_for_reduce(extractions)
    if len(groups) == 1:
        body = format_extractions(next(iter(groups.values())), papers_by_key)
        if not body:
            logger.warning(
                "[reduce] 추출 %d건이 있으나 papers_by_key 매칭 실패로 본문이 비어 LLM 호출을 건너뜀",
                len(extractions),
            )
            return no_data_message
        return await gemini_sync.generate(
            REDUCE_INSTRUCTION, header + body, thinking=settings.thinking_reduce
        )

    partials: list[str] = []
    for name, items in groups.items():
        body = format_extractions(items, papers_by_key)
        if not body:
            continue
        partial = await gemini_sync.generate(
            REDUCE_INSTRUCTION, f"[성과유형: {name}]\n{body}", thinking=settings.thinking_reduce
        )
        partials.append(f"### {name}\n{partial}")

    if not partials:
        logger.warning(
            "[reduce] 추출 %d건이 있으나 모든 그룹에서 papers_by_key 매칭 실패로 본문이 비어 LLM 호출을 건너뜀",
            len(extractions),
        )
        return no_data_message

    return await gemini_sync.generate(
        REDUCE_INSTRUCTION,
        header
        + "아래는 성과유형별 중간 정리 결과입니다. 이를 하나의 보고서로 통합하세요.\n\n"
        + "\n\n".join(partials),
        thinking=settings.thinking_reduce,
    )


async def rollup_field(field_name: str, subfield_reports: list[tuple[str, str]]) -> str:
    """대분류 보고서 = 하위 세부기술 보고서 합성 1콜."""
    if not subfield_reports:
        return "분석된 세부기술이 없습니다."

    body = "\n\n".join(f"## {name}\n{report}" for name, report in subfield_reports)
    return await gemini_sync.generate(
        ROLLUP_INSTRUCTION, f"[대분류: {field_name}]\n\n{body}", thinking=settings.thinking_reduce
    )
