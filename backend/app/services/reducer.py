import asyncio
import json
import logging
import re
from collections import Counter, defaultdict

from sqlalchemy.orm import Session

from app.services import _time
from app.clients import gemini_sync
from app.services import comparison
from app.config import settings
from app.models.analysis import Analysis
from app.models.field import Field, FieldReport, Roadmap, RoadmapCheck, Subfield
from app.models.paper import Paper, PaperExtraction
from app.prompts import (
    REDUCE_INSTRUCTION,
    ROADMAP_NARRATIVE_INSTRUCTION,
    ROLLUP_INSTRUCTION,
    country_name,
    roadmap_row_instruction,
    roadmap_row_schema,
    roadmap_row_user_text,
    roadmap_row_verdict,
)
from app.services.roadmap_parse import count_goal_rows, parse_goals  # noqa: F401

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


# 분야 종합·로드맵 점검이 대조하는 국가. `roadmap_checks`·`field_reports`는 키가
# (field_id, year)뿐이라 국가를 담지 못한다 — 다국 지원 이전 설계다. 이 상수가 그
# 빈자리를 메운다. 국가별 분야 보고서가 필요해지면 두 테이블에 country를 넣고 unique
# 제약을 (field_id, year, country)로 넓혀야 한다.
REPORT_COUNTRY = comparison._BASE_COUNTRY


def collect_subfield_reports(
    db: Session, field_id: int, year: int, country: str = REPORT_COUNTRY
) -> list[tuple[str, str]]:
    """이 분야·연도·**국가**에서 완성된 세부기술 보고서 (이름, 본문) 목록.

    done인데 report_md가 비어 있는 행(분석 대상 논문 0건)은 제외한다 —
    합성에 넣어봐야 모델이 근거 없이 채워 넣을 여지만 준다.

    **국가 필터는 필수다.** 다국 지원이 들어온 뒤로 이 필터가 없어 한 세부기술의 보고서가
    국가 수만큼 중복으로 실렸다 — 실측(2026-09-05): 반도체 2026에서 세부기술 10개인데
    보고서 40건이 나왔고, 그 본문에는 국가가 박혀 있다("2026년 **중국의** 차세대 메모리
    반도체 연구는…"). 즉 **한국 로드맵을 중국·일본·미국 연구 서술과 대조**하고 있었다.
    같은 세부기술 제목이 네 번 반복되므로 모델 입장에서도 입력이 망가진다.
    """
    rows = (
        db.query(Subfield.name, Analysis.report_md)
        .join(Analysis, Analysis.subfield_id == Subfield.id)
        .filter(
            Subfield.field_id == field_id,
            Analysis.year == year,
            Analysis.country == country,
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
        row = FieldReport(field_id=field_id, year=year, generated_at=_time.utcnow())
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
    row.generated_at = _time.utcnow()
    row.source_count = len(reports)
    row.status = "done"
    row.error = None
    db.commit()


# 마크다운 표의 구분선(|---|:---:|) — 이 줄 다음부터가 본문 행이다. 헤더 행은
# 구분선보다 앞에 오므로 자연히 세어지지 않는다.


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
        row = RoadmapCheck(field_id=field_id, year=year, generated_at=_time.utcnow())
        db.add(row)
    row.status = "pending"
    row.error = None
    db.commit()
    db.refresh(row)
    return row


# 행 단위 판정의 동시 실행 수. gemini_sync의 RPM 버킷이 실제 발사 속도를 잡으므로
# 여기서는 커넥션·메모리만 막으면 된다. 65행이 이 값으로 나뉘어 도는데, 잡 루프는
# 한 틱에 보고서 하나만 처리하므로 다른 잡을 굶기지 않는다.
ROADMAP_ROW_CONCURRENCY = 6


_WS = re.compile(r"\s+")


def excerpt_grounded(sentence: str, context: str) -> bool:
    """발췌가 (B)에 실제로 있는가. 공백 차이는 무시하고 20자 이상 연속 일치면 인정한다.

    이것이 절차 판정의 핵심 배당이다 — 근거를 "(B) 문장 그대로"로 강제했으니 코드가
    찾아볼 수 있고, 못 찾으면 모델이 지어낸 것이다. 자유 문장 근거에서는 불가능했다.
    """
    # 모델이 문장 끝을 "..."로 자르는 일이 잦다 — 운영 첫 실행에서 ⚠ 4건 중 3건이 그것.
    # 말줄임표는 (B)에 없으니 떼고 본다.
    a = _WS.sub("", (sentence or "").rstrip(".…·"))
    b = _WS.sub("", context)
    if len(a) < 12:
        return bool(a) and a in b
    # 창 20자 · 보폭 4자. 보폭 10으로는 창 경계가 어긋나 실재하는 문장을 놓쳤다(실측 1건).
    # 발췌는 수십 자라 보폭을 줄여도 비용이 없다.
    return any(a[k:k + 20] in b for k in range(0, max(1, len(a) - 19), 4))


async def judge_goals(
    goals: list[dict], context: str, present: list[str], missing: list[str]
) -> list[dict]:
    """목표 행마다 독립 콜로 **절차 판정**한다. 실패한 행은 판정 없이 남긴다(전체를 죽이지 않는다).

    돌려주는 각 행에 `하위목표`(항목·발췌·판정)와 `판정`이 붙고, 판정은 모델 출력이 아니라
    `roadmap_row_verdict`로 **다시 계산한 값**이다. 모델이 규칙과 다르게 말했으면
    `규칙위반=True`로 표시만 하고 규칙을 따른다. 발췌마다 `실재` 플래그를 붙인다.
    """
    system = roadmap_row_instruction(present, missing)
    schema = roadmap_row_schema(missing)
    sem = asyncio.Semaphore(ROADMAP_ROW_CONCURRENCY)

    async def one(goal: dict) -> dict:
        async with sem:
            try:
                text = await gemini_sync.generate(
                    system,
                    roadmap_row_user_text(goal, context),
                    thinking=settings.thinking_reduce,
                    schema=schema,
                )
                data = json.loads(text)
                subs = data.get("하위목표") or []
                for sg in subs:
                    for e in sg.get("발췌") or []:
                        e["실재"] = excerpt_grounded(e.get("문장", ""), context)
                verdict = roadmap_row_verdict(subs, data.get("판정"), missing)
                return {**goal, "하위목표": subs, "판정": verdict,
                        "규칙위반": data.get("판정") != verdict}
            except Exception as e:  # 한 행의 실패가 보고서 전체를 막지 않는다
                logger.warning("[roadmap] 목표 %s 판정 실패: %s", goal["id"], e)
                return {**goal, "하위목표": [], "판정": None, "규칙위반": False}

    return list(await asyncio.gather(*[one(g) for g in goals]))


_CIRC = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"


def _cell(t: str) -> str:
    return (t or "").replace("|", "\\|").replace("\n", " ")


def _assemble_roadmap_report(
    judged: list[dict], present: list[str], missing: list[str], narrative: str
) -> str:
    """개요와 표·발췌 목록은 **코드가 쓴다.** LLM은 서술 두 절만 만든다.

    표는 행당 한 줄(개요 — `count_goal_rows`가 세는 대상)이고, 그 아래 `#### 근거 발췌`
    목록이 하위 목표별 판정과 발췌를 싣는다. 둘로 나눈 이유: 화면 렌더러(react-markdown +
    remark-gfm, rehype-raw 없음)가 표 셀 안의 줄바꿈을 못 그린다. (B)에서 찾지 못한 발췌는
    `⚠ (B)에서 확인 안 됨`으로 표시한다 — 감추면 이 구조의 존재 이유가 사라진다.
    """
    tally = Counter(j["판정"] for j in judged if j["판정"])
    failed = sum(1 for j in judged if not j["판정"])
    subs_all = [sg for j in judged for sg in j.get("하위목표") or []]
    sub_tally = Counter(sg.get("판정") for sg in subs_all)
    excerpts = [e for sg in subs_all for e in sg.get("발췌") or []]
    ungrounded = sum(1 for e in excerpts if not e.get("실재"))
    violations = sum(1 for j in judged if j.get("규칙위반"))
    order = [v for v in ("관련 연구 확인", "부분 관련", "데이터 없음", "분석 범위 밖") if tally.get(v)]

    out = ["## 1. 점검 개요", ""]
    out.append(f"이 점검은 로드맵의 기술적 목표 **{len(judged)}개**를 세부기술별 성과 보고서 "
               f"**{len(present)}건**과 하나씩 대조한 결과입니다. 목표는 **{len(subs_all)}개 "
               f"하위 목표**로 나누어 각각 근거를 찾았습니다.")
    out.append("")
    out.append(f"- 대조에 사용한 세부기술 보고서: {', '.join(present)}")
    if missing:
        out.append(f"- **보고서가 없어 대조하지 못한 세부기술: {', '.join(missing)}**")
    out.append("- 목표 행 판정: " + " · ".join(
        f"`{v}` {tally[v]}건({tally[v] / len(judged):.0%})" for v in order))
    if subs_all:
        d = sub_tally.get("직접", 0)
        out.append(f"- 하위 목표 판정: `직접` {d}개({d / len(subs_all):.0%}) · "
                   f"`인접` {sub_tally.get('인접', 0)}개 · `없음` {sub_tally.get('없음', 0)}개 "
                   f"— **직접 근거가 있는 하위 목표의 비율이 행 단위 확인율보다 정직한 수치입니다** "
                   f"(한 행은 하위 목표 하나만 맞아도 `관련 연구 확인`이 되므로)")
    out.append(f"- 근거 발췌 {len(excerpts)}건 — 보고서 원문에서 확인된 것 {len(excerpts) - ungrounded}건"
               + (f", **확인되지 않은 것 {ungrounded}건(⚠ 표시)**" if ungrounded else ""))
    if violations:
        out.append(f"- 규칙과 다르게 판정한 행 {violations}건 — 규칙값으로 바로잡았습니다")
    if failed:
        out.append(f"- **판정 실패 {failed}건** — 재생성이 필요합니다")
    out.append("")
    out.append("행 판정은 하위 목표 판정에서 규칙으로 정해집니다(`직접`이 하나라도 있으면 `관련 연구 확인`, "
               "`인접`만 있으면 `부분 관련`, 전부 `없음`이면 `데이터 없음`). 발췌는 보고서 문장을 그대로 "
               "옮긴 것이며 같은 입력에서 같은 결과가 나옵니다.")
    out += ["", "## 2. 중점기술별 목표 점검", ""]

    for section in dict.fromkeys(j["중점기술"] for j in judged):
        rows = [j for j in judged if j["중점기술"] == section]
        out += [f"### {section}", "",
                "| 단계·구분 | 기술적 목표 | 판정 | 하위 목표 |",
                "| --- | --- | --- | --- |"]
        for j in rows:
            item = f"{j['세부항목']} · " if j["세부항목"] != j["중점기술"] else ""
            stage = f"{item}{j['단계']}" + (f" ({j['시기']})" if j["시기"] else "")
            subs = j.get("하위목표") or []
            c = Counter(sg.get("판정") for sg in subs)
            tally_s = " · ".join(f"{k} {c[k]}" for k in ("직접", "인접", "없음") if c.get(k))
            sub_cell = f"{len(subs)}개 — {tally_s}" if subs else "—"
            verdict = f"**{j['판정']}**" if j["판정"] else "**판정 실패**"
            out.append(f"| {_cell(stage)} | {_cell(j['목표'])} | {verdict} | {_cell(sub_cell)} |")
        out += ["", "#### 근거 발췌", ""]
        for j in rows:
            head = j["세부항목"] if j["세부항목"] != j["중점기술"] else j["단계"]
            out.append(f"- **[{j['id']}] {head}** → {j['판정'] or '판정 실패'}")
            for k, sg in enumerate(j.get("하위목표") or []):
                mark = _CIRC[k] if k < len(_CIRC) else f"({k + 1})"
                line = f"  - {mark} {sg.get('항목', '')} → **{sg.get('판정', '')}**"
                ex = (sg.get("발췌") or [])[:1]
                if ex:
                    e = ex[0]
                    q = (e.get("문장") or "")[:120]
                    if len(e.get("문장") or "") > 120:
                        q += "…"
                    line += f" — [{e.get('세부기술', '')}] “{q}”"
                    if e.get("수치"):
                        line += f" `{e['수치']}`"
                    if not e.get("실재"):
                        line += " ⚠ (B)에서 확인 안 됨"
                out.append(line)
        out.append("")
    return "\n".join(out) + "\n" + narrative.strip() + "\n"


async def process_roadmap_check(db: Session, row: RoadmapCheck) -> None:
    """pending RoadmapCheck 하나를 실제로 생성한다. runner.loop이 호출한다.

    **목표 행마다 독립 콜로 판정하고 표는 코드가 조립한다.** 예전에는 65행 표 전체를
    한 콜로 만들었는데 temperature 0에서도 재현되지 않았다 — 실측(2026-09-05, 반도체
    2026 KR): 같은 입력 두 번에 27/65행이 달랐고 `데이터 없음`이 43→22건으로 판정
    기조가 통째로 넘어갔다. 행 단위는 같은 조건에서 65/65 완전 재현이다.

    ⚠ 로드맵 원문이 그대로 Gemini API로 전송된다. 외부로 내보낼 수 없는 판본을 다뤄야
    하면 여기서 부르는 gemini_sync.generate를 로컬 모델 클라이언트로 분기하면 된다 —
    프롬프트·저장 구조는 그대로 쓸 수 있다.
    """
    field = db.get(Field, row.field_id)
    roadmap = db.query(Roadmap).filter(Roadmap.field_id == row.field_id).one_or_none()
    if roadmap is None:
        raise ValueError(f"{field.name if field else row.field_id}에 로드맵이 없습니다.")
    reports = collect_subfield_reports(db, row.field_id, row.year)
    if not reports:
        raise ValueError(f"{field.name if field else row.field_id} {row.year}년에 세부기술 보고서가 없습니다.")

    goals = parse_goals(roadmap.content_md)
    present = [name for name, _ in reports]
    all_names = [s.name for s in db.query(Subfield)
                 .filter(Subfield.field_id == row.field_id).all()]
    missing = [n for n in all_names if n not in present]
    context = "\n\n".join(f"## {name}\n{md}" for name, md in reports)

    logger.info("[roadmap] %s %d년 — 목표 %d행 × 세부기술 보고서 %d건 (행 단위 판정)",
                field.name, row.year, len(goals), len(reports))
    judged = await judge_goals(goals, context, present, missing)

    def _summ(j: dict) -> str:
        subs = j.get("하위목표") or []
        parts = "; ".join(f"{sg.get('항목', '')}→{sg.get('판정', '')}" for sg in subs)
        return (f"{j['id']}. [{j['중점기술']} · {j['단계']}] {j['목표']}\n"
                f"   → {j['판정'] or '판정 실패'} ({parts})")

    narrative = await gemini_sync.generate(
        ROADMAP_NARRATIVE_INSTRUCTION,
        "\n".join(_summ(j) for j in judged),
        thinking=settings.thinking_reduce,
    )
    report_md = _assemble_roadmap_report(judged, present, missing, narrative)

    # 표를 코드가 쓰므로 행 수는 정의상 맞는다. 그래도 계속 세는 이유: 조립 로직이
    # 깨지면(마크다운 이스케이프 실수 등) 조용히 행이 사라질 수 있고, 화면의 경고가
    # 그것을 잡는 마지막 그물이다.
    checked_count = count_goal_rows(report_md)
    if checked_count != len(goals):
        logger.warning("[roadmap] 표 조립 불일치 — 목표 %d행 대비 표 %d행",
                       len(goals), checked_count)

    row.report_md = report_md
    row.generated_at = _time.utcnow()
    row.source_count = len(reports)
    row.goal_count = len(goals)
    row.checked_count = checked_count
    row.roadmap_version = roadmap.version_label
    row.status = "done"
    row.error = None
    db.commit()
