import logging
import re
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.clients import gemini_sync
from app.config import settings
from app.models.analysis import Analysis
from app.models.field import Field, FieldReport, Roadmap, RoadmapCheck, Subfield
from app.models.paper import Paper, PaperExtraction
from app.prompts import (
    REDUCE_INSTRUCTION,
    ROADMAP_CHECK_INSTRUCTION,
    ROLLUP_INSTRUCTION,
    country_name,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """DB의 naive DateTime 컬럼에 맞춘 현재 UTC 시각(tzinfo 제거)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
) -> tuple[str, list[dict]]:
    """세부기술 보고서를 만들고 (최종 보고서, 그룹별 중간 보고서)를 돌려준다.

    3단 reduce의 partial을 버리지 않는 이유: 최종 통합이 partial을 다시 압축하는
    이중 압축이 500건 이상에서 인용률이 무너지는 직접 원인이다(실측: 단일 reduce
    350~499구간 9.7% → 3단 500~799구간 5.6%). 화면이 이것을 펼쳐 보여준다.
    단일 reduce는 그룹이 하나뿐이라 두 번째 원소가 빈 리스트다.

    추출 결과가 0건이거나, 있어도 papers_by_key 매칭 실패로 LLM에 보낼 본문이 비면
    LLM을 호출하지 않는다 — 빈 입력으로 부르면 모델이 성과를 통째로 지어낸다.
    """
    no_data_message = "분석 대상 논문이 없어 성과를 정리할 수 없습니다."
    if not extractions:
        return no_data_message, []

    # 대상 세부기술명을 입력에 명시한다. 없으면 모델이 본문 내용만 보고 H1 제목을 새로
    # 지어내, 목록 화면의 세부기술명과 보고서 제목이 어긋난다(실측: "재생에너지" 분석의
    # 보고서 제목이 "에너지 변환 및 자원 순환 공학"으로 나왔다). 3단 reduce는 최종 합성
    # 입력이 중간 요약뿐이라 세부기술명이 아예 사라져 더 크게 어긋난다.
    subfield = db.get(Subfield, analysis.subfield_id)
    header = (
        f"[세부기술: {subfield.name if subfield else '미상'} / {analysis.year}"
        f" / {country_name(analysis.country)}]\n"
    )

    groups = group_for_reduce(extractions)
    if len(groups) == 1:
        body = format_extractions(next(iter(groups.values())), papers_by_key)
        if not body:
            logger.warning(
                "[reduce] 추출 %d건이 있으나 papers_by_key 매칭 실패로 본문이 비어 LLM 호출을 건너뜀",
                len(extractions),
            )
            return no_data_message, []
        report = await gemini_sync.generate(
            REDUCE_INSTRUCTION, header + body, thinking=settings.thinking_reduce
        )
        return report, []

    partials: list[str] = []
    sections: list[dict] = []
    for name, items in groups.items():
        body = format_extractions(items, papers_by_key)
        if not body:
            continue
        partial = await gemini_sync.generate(
            REDUCE_INSTRUCTION, f"[성과유형: {name}]\n{body}", thinking=settings.thinking_reduce
        )
        partials.append(f"### {name}\n{partial}")
        sections.append({"name": name, "body": partial})

    if not partials:
        logger.warning(
            "[reduce] 추출 %d건이 있으나 모든 그룹에서 papers_by_key 매칭 실패로 본문이 비어 LLM 호출을 건너뜀",
            len(extractions),
        )
        return no_data_message, []

    report = await gemini_sync.generate(
        REDUCE_INSTRUCTION,
        header
        + "아래는 성과유형별 중간 정리 결과입니다. 이를 하나의 보고서로 통합하세요.\n\n"
        + "\n\n".join(partials),
        thinking=settings.thinking_reduce,
    )
    return report, sections


async def rollup_field(field_name: str, subfield_reports: list[tuple[str, str]]) -> str:
    """대분류 보고서 = 하위 세부기술 보고서 합성 1콜."""
    if not subfield_reports:
        return "분석된 세부기술이 없습니다."

    body = "\n\n".join(f"## {name}\n{report}" for name, report in subfield_reports)
    return await gemini_sync.generate(
        ROLLUP_INSTRUCTION, f"[대분류: {field_name}]\n\n{body}", thinking=settings.thinking_reduce
    )


def collect_subfield_reports(db: Session, field_id: int, year: int) -> list[tuple[str, str]]:
    """이 분야·연도에서 완성된 세부기술 보고서 (이름, 본문) 목록.

    done인데 report_md가 비어 있는 행(분석 대상 논문 0건)은 제외한다 —
    합성에 넣어봐야 모델이 근거 없이 채워 넣을 여지만 준다.
    """
    rows = (
        db.query(Subfield.name, Analysis.report_md)
        .join(Analysis, Analysis.subfield_id == Subfield.id)
        .filter(
            Subfield.field_id == field_id,
            Analysis.year == year,
            Analysis.status == "done",
            Analysis.report_md.isnot(None),
        )
        .order_by(Subfield.name)
        .all()
    )
    return [(name, md) for name, md in rows if md]


def enqueue_field_report(db: Session, field_id: int, year: int) -> FieldReport:
    """분야 종합 보고서를 pending으로 큐잉한다(실제 LLM 호출은 runner가 한다).

    없는 분야면 LookupError(→404), 완성된 세부기술 보고서가 하나도 없으면 ValueError(→409).
    검증을 여기(큐잉 시점)서 해 관리자가 즉시 에러를 받게 한다 — 큐잉해 놓고 나중에
    조용히 failed되는 것보다 낫다.

    재생성이면 기존 report_md·통계는 그대로 두고 status만 pending으로 되돌린다 —
    처리가 끝나기 전까지 이전 보고서를 계속 보여주기 위해서다.
    """
    field = db.get(Field, field_id)
    if field is None:
        raise LookupError(f"분야 {field_id}를 찾을 수 없습니다.")
    if not collect_subfield_reports(db, field_id, year):
        raise ValueError(f"{field.name} {year}년에 완성된 세부기술 보고서가 없습니다.")

    row = (
        db.query(FieldReport)
        .filter(FieldReport.field_id == field_id, FieldReport.year == year)
        .one_or_none()
    )
    if row is None:
        row = FieldReport(field_id=field_id, year=year, generated_at=_utcnow())
        db.add(row)
    row.status = "pending"
    row.error = None
    db.commit()
    db.refresh(row)
    return row


async def process_field_report(db: Session, row: FieldReport) -> None:
    """pending FieldReport 하나를 실제로 생성한다. runner.loop이 호출한다.

    빈 입력으로 LLM을 부르면 분야 성과를 통째로 지어내므로(reduce_subfield의 no_data
    가드와 같은 이유), 처리 시점에 세부기술 보고서가 사라졌으면 ValueError를 던진다 —
    runner가 이를 잡아 failed로 남긴다.
    """
    field = db.get(Field, row.field_id)
    reports = collect_subfield_reports(db, row.field_id, row.year)
    if not reports:
        raise ValueError(f"{field.name if field else row.field_id} {row.year}년에 세부기술 보고서가 없습니다.")

    logger.info("[rollup] %s %d년 — 세부기술 보고서 %d건 합성", field.name, row.year, len(reports))
    row.report_md = await rollup_field(field.name, reports)
    row.generated_at = _utcnow()
    row.source_count = len(reports)
    row.status = "done"
    row.error = None
    db.commit()


# 마크다운 표의 구분선(|---|:---:|) — 이 줄 다음부터가 본문 행이다. 헤더 행은
# 구분선보다 앞에 오므로 자연히 세어지지 않는다.
_TABLE_SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|$")


def count_goal_rows(md: str) -> int:
    """로드맵 마크다운에서 목표 행(표 본문 행) 수를 센다.

    이 값을 ROADMAP_CHECK_INSTRUCTION에 주입해 전수 점검을 강제한다. 모델에게
    세라고 시키지 않고 코드로 세는 이유: 개수를 못박지 않으면 모델이 여러 단계를
    한 행으로 합쳐버린다(실측 65행 → 19행).
    """
    count = 0
    in_table = False
    for line in md.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            in_table = False
            continue
        if _TABLE_SEPARATOR_RE.match(line):
            in_table = True
            continue
        if in_table:
            count += 1
    return count


def enqueue_roadmap_check(db: Session, field_id: int, year: int) -> RoadmapCheck:
    """로드맵 이행 점검을 pending으로 큐잉한다(실제 LLM 호출은 runner가 한다).

    없는 분야면 LookupError(→404). 로드맵이 없거나(표 형식이 아니거나) 완성된 세부기술
    보고서가 없으면 ValueError(→409). 검증을 큐잉 시점에 해 관리자가 즉시 에러를 받게 한다.
    """
    field = db.get(Field, field_id)
    if field is None:
        raise LookupError(f"분야 {field_id}를 찾을 수 없습니다.")

    roadmap = db.query(Roadmap).filter(Roadmap.field_id == field_id).one_or_none()
    if roadmap is None:
        raise ValueError(f"{field.name}에 등록된 전략기술로드맵이 없습니다.")
    if count_goal_rows(roadmap.content_md) == 0:
        raise ValueError(
            "로드맵에서 목표 행을 찾지 못했습니다. 단계별 목표가 마크다운 표 형식인지 확인하세요."
        )
    if not collect_subfield_reports(db, field_id, year):
        raise ValueError(f"{field.name} {year}년에 완성된 세부기술 보고서가 없습니다.")

    row = (
        db.query(RoadmapCheck)
        .filter(RoadmapCheck.field_id == field_id, RoadmapCheck.year == year)
        .one_or_none()
    )
    if row is None:
        row = RoadmapCheck(field_id=field_id, year=year, generated_at=_utcnow())
        db.add(row)
    row.status = "pending"
    row.error = None
    db.commit()
    db.refresh(row)
    return row


async def process_roadmap_check(db: Session, row: RoadmapCheck) -> None:
    """pending RoadmapCheck 하나를 실제로 생성한다. runner.loop이 호출한다.

    ⚠ 로드맵 원문이 그대로 Gemini API로 전송된다. 외부로 내보낼 수 없는 판본을
    다뤄야 하면 여기서 부르는 gemini_sync.generate를 로컬 모델 클라이언트로
    분기하면 된다 — 프롬프트·전수 점검 검증·저장 구조는 그대로 쓸 수 있다.
    다만 65행 전수 점검을 로컬 모델이 지켜내는지는 별도 검증이 필요하다.
    """
    field = db.get(Field, row.field_id)
    roadmap = db.query(Roadmap).filter(Roadmap.field_id == row.field_id).one_or_none()
    if roadmap is None:
        raise ValueError(f"{field.name if field else row.field_id}에 로드맵이 없습니다.")
    reports = collect_subfield_reports(db, row.field_id, row.year)
    if not reports:
        raise ValueError(f"{field.name if field else row.field_id} {row.year}년에 세부기술 보고서가 없습니다.")

    goal_count = count_goal_rows(roadmap.content_md)
    body = (
        "# (A) 전략기술로드맵\n\n" + roadmap.content_md
        + "\n\n\n# (B) 논문 분석 기반 세부기술별 성과 보고서\n\n"
        + "\n\n".join(f"## {name}\n{md}" for name, md in reports)
    )
    logger.info(
        "[roadmap] %s %d년 — 목표 %d행 × 세부기술 보고서 %d건",
        field.name, row.year, goal_count, len(reports),
    )
    report_md = await gemini_sync.generate(
        ROADMAP_CHECK_INSTRUCTION.format(goal_count=goal_count),
        body,
        thinking=settings.thinking_reduce,
    )

    # 전수 점검이 실제로 지켜졌는지 코드로 검증한다. 지시만으로는 조용히 줄어들 수
    # 있고(실측), 줄어든 보고서는 "빠진 목표가 없다"로 오독된다. 실패해도 보고서는
    # 남기되 그 사실을 함께 저장해 화면에서 경고한다 — 재생성은 관리자가 판단한다.
    checked_count = count_goal_rows(report_md)
    if checked_count != goal_count:
        logger.warning(
            "[roadmap] 전수 점검 불일치 — 목표 %d행 대비 점검 %d행", goal_count, checked_count
        )

    row.report_md = report_md
    row.generated_at = _utcnow()
    row.source_count = len(reports)
    row.goal_count = goal_count
    row.checked_count = checked_count
    row.roadmap_version = roadmap.version_label
    row.status = "done"
    row.error = None
    db.commit()
