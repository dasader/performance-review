import re
from datetime import datetime, timezone
from typing import Any, Sequence

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.clients._html import strip_html
from app.config import settings
from app.database import get_db
from app.models.analysis import Analysis, AnalysisPaper
from app.models.field import Field, FieldReport, Roadmap, RoadmapCheck, Subfield
from app.models.paper import Paper
from app.services import visitors as visitors_service
from app.services.runner import STEP_LABELS

router = APIRouter(prefix="/api", tags=["public"])

# 한 단계 중첩까지 허용한다: 인용문 안에 "TrioN (3N0C)"처럼 논문 제목 자체가 괄호를
# 품는 경우, 바깥 인용 괄호 전체를 잡아야지 안쪽 괄호만 잡으면 안 된다(그러면 바깥 인용
# 전체가 미치환으로 남는다). `(?:[^()]|\([^()]*\))*` — 괄호가 아닌 문자이거나, 괄호가 없는
# 내용의 중첩 괄호 한 겹. 두 단계 이상 중첩은 다루지 않는다 — 실제 인용 데이터는 한 겹이면
# 충분하고, 그 이상을 지원하려면 재귀적 괄호 파서가 필요해 복잡도만 커진다.
# 인용 형식 두 가지를 함께 잡는다.
#
# ① 괄호 — 한 단계 중첩까지 허용한다: 인용문 안에 "TrioN (3N0C)"처럼 논문 제목 자체가
#    괄호를 품는 경우, 바깥 인용 괄호 전체를 잡아야지 안쪽 괄호만 잡으면 안 된다(그러면
#    바깥 인용 전체가 미치환으로 남는다). `(?:[^()]|\([^()]*\))*` — 괄호가 아닌 문자이거나,
#    괄호가 없는 내용의 중첩 괄호 한 겹. 두 단계 이상 중첩은 다루지 않는다 — 실제 인용
#    데이터는 한 겹이면 충분하고, 그 이상은 재귀적 괄호 파서가 필요해 복잡도만 커진다.
# ② 백틱(코드 스팬) — LLM이 괄호 대신 이 형식으로 인용하는 경우가 실제로 있다.
#    실측(안전·신뢰 AI 2026): 서술부 인용 26건 중 23건이 백틱이고 괄호는 3건뿐이라,
#    괄호만 보던 매칭이 제목을 통째로 노출시켰다. 백틱 안에는 코드·용어도 들어오지만
#    치환은 "내용이 실제 논문 제목과 맞는가"로 판정하므로(_MIN_PARTIAL_TITLE_LEN 포함)
#    제목이 아닌 코드 스팬은 그대로 남는다.
_CITE_RE = re.compile(r"\(((?:[^()]|\([^()]*\))+)\)|`([^`\n]+)`")

# 치환 후 "*   [\[1\]](#ref-1)"처럼 인용만 남는 불릿 — LLM이 인용을 문단 안이 아니라
# 목록으로 나열하면 번호만 있는 항목이 줄줄이 쌓인다(실측 subfield 8 / 2026: 서술부
# 불릿 30줄). 연속된 그런 줄을 한 문단으로 접는다. 본문이 있는 불릿은 건드리지 않는다.
_FOOTNOTE_LINK = r"\[\\\[\d+\\\]\]\(#ref-\d+\)"
_FOOTNOTE_ONLY_BULLETS_RE = re.compile(
    rf"(?:^[ \t]*[-*][ \t]+(?:{_FOOTNOTE_LINK}[ \t]*)+$\n?)+", re.M
)

# 짧은 제목(정규화 키 기준 15자 미만)은 괄호 안 텍스트에 부분 문자열로 우연히 등장할
# 위험이 커서(예: 제목이 "AI"면 "(AI 기반 진단 시스템)"에도 걸림) 부분 매칭 대상에서
# 제외하고 완전 일치만 허용한다. 15는 실측 데이터(짧아도 단어 2~3개는 되는 실제 논문
# 제목들) 기준의 여유 있는 하한선이다 — 과학적으로 도출된 값은 아니다.
_MIN_PARTIAL_TITLE_LEN = 15


def _footnote_key(s: str) -> str:
    """각주 매칭용 비교 키: strip_html로 태그를 벗긴 뒤 공백을 전부 제거하고 소문자화한다.

    OpenAlex 원문은 태그 자리에 공백이 있을 때도("Hf <sub>0.5</sub> Zr" -> "Hf 0.5 Zr")
    없을 때도("MoS<sub>2</sub>" -> "MoS2") 있어 DB에 저장된 제목과 LLM이 실제로 인용한
    문자열 사이에 공백 개수가 어긋난다("Hf 0.5 Zr 0.5 O 2" vs "Hf0.5Zr0.5O2"). 공백을
    "하나로 접기"가 아니라 완전히 제거해야 이 둘이 같은 키로 만난다. 길이 판단
    (_MIN_PARTIAL_TITLE_LEN)도 이 키 기준으로 한다 — 실제 오탐 위험은 원문 글자 수가
    아니라 이 키(부분 문자열 검사에 실제로 쓰이는 문자열)의 길이에 달려 있다."""
    return "".join(strip_html(s).split()).lower()


def _apply_footnotes(report_md: str | None, papers: Sequence[Any]) -> tuple[str | None, list[dict]]:
    """report_md 안에서 괄호로 인용된 논문 제목을 각주 번호로 치환한다(읽기 시점 후처리,
    LLM 재호출 없음). LLM이 실행마다 인용 형식을 조금씩 다르게 쓰므로(연도 접두사, 공백 차이,
    구버전 보고서에 남은 HTML 태그 등) 매칭은 정규화 키("괄호 안 어딘가에 제목의 키가
    포함되는가")로 본다 — 괄호 전체와 정확히 같아야만 치환하던 이전 방식은 `([2025] 제목)`
    같은 형태를 전혀 못 잡았고, 단어를 `\\s+`로 이어 붙인 정규식 방식은 공백 개수 자체가
    달라지는 경우(OpenAlex 원문 태그 공백, 구버전 보고서의 남은 태그)를 못 잡았다. 다만 짧은
    제목은 부분 매칭 시 오탐 위험이 커서(_MIN_PARTIAL_TITLE_LEN 참고) 키 완전 일치만
    허용한다. 못 찾은 제목은 원문 그대로 둔다 — 잘못 치환하는 것보다 안전하다.

    제목은 긴 것부터(키 길이 기준) 시도한다. 한 제목이 다른 제목의 부분 문자열인 경우(예:
    "Graphene Growth Method"가 "Advanced Graphene Growth Method for Flexible Devices"에
    포함), 짧은 쪽을 먼저 보면 실제로는 긴 제목이 인용된 괄호가 짧은 제목의 논문으로 잘못
    귀속된다.

    각주는 `[\\[1\\]](#ref-1)` 형태의 마크다운 링크로 치환된다 — 대괄호를 이스케이프해
    렌더링 시 보이는 텍스트는 그대로 "[1]"이면서 "#ref-1"(참고문헌 항목)로 이동하는 링크가
    된다. 첫 인용 지점에 돌아올 앵커(id="cite-1")를 붙이는 책임은 프론트(Report.tsx)에
    있다 — 같은 논문이 여러 번 인용될 수 있어 "첫 인용 지점"을 여기(파이썬)에서 가리려면
    별도 상태 추적이 필요하지만, 프론트는 이미 각 인용 링크를 렌더링하는 시점에 있으므로
    처음 본 #ref-n을 추적하는 쪽이 더 단순하다(추가 마크업/원시 HTML도 필요 없음)."""
    if not report_md:
        return report_md, []

    # (paper, 정규화 키) 목록. 키 길이 내림차순 — 위 docstring의 부분 문자열 문제 방지.
    candidates: list[tuple[Any, str]] = sorted(
        ((p, _footnote_key(p.title)) for p in papers if p.title and p.title.strip()),
        key=lambda pc: len(pc[1]),
        reverse=True,
    )

    numbers: dict[int, int] = {}  # paper.id -> 각주 번호
    references: list[dict] = []

    def repl(m: re.Match) -> str:
        # group(1)=괄호 인용, group(2)=백틱 인용. 둘 중 매칭된 쪽을 쓴다.
        content = m.group(1) if m.group(1) is not None else m.group(2)
        content_key = _footnote_key(content)
        matched: Any | None = None
        for paper, key in candidates:
            if len(key) < _MIN_PARTIAL_TITLE_LEN:
                if content_key != key:
                    continue
            elif key not in content_key:
                continue
            matched = paper
            break
        if matched is None:
            return m.group(0)
        n = numbers.get(matched.id)
        if n is None:
            n = len(references) + 1
            numbers[matched.id] = n
            references.append({
                "n": n,
                "title": matched.title,
                "journal": matched.journal,
                "year": matched.year,
                "doi": matched.doi,
            })
        return f"[\\[{n}\\]](#ref-{n})"

    substituted = _CITE_RE.sub(repl, report_md)

    def collapse(m: re.Match) -> str:
        """인용만 남은 불릿 묶음을 각주가 이어진 한 문단으로 접는다."""
        links = re.findall(_FOOTNOTE_LINK, m.group(0))
        return "".join(links) + "\n"

    return _FOOTNOTE_ONLY_BULLETS_RE.sub(collapse, substituted), references


def _footnoted_report(db: Session, analysis: Analysis) -> tuple[str | None, list[dict]]:
    """analysis의 report_md에 각주 치환을 적용하고 (치환된 md, references)를 돌려준다.

    report_md 원문은 "(논문 제목)" 형태로 저장돼 있고, 각주 [n] 치환은 조회 시점에
    한다 — 세부기술 보고서 화면과 분야 종합보고서 부록(세부기술 첨부)이 이걸 공유한다.
    빼먹으면 논문 제목이 full name 그대로 노출된다."""
    # 각주는 id/title/journal/year/doi만 쓴다. 전체 ORM 행을 실으면 abstract(논문당
    # 1~2KB)까지 딸려와, 703건짜리 보고서 한 번 여는 데 1MB 가까이 헛읽는다.
    papers = db.query(
        Paper.id, Paper.title, Paper.journal, Paper.year, Paper.doi
    ).join(AnalysisPaper, AnalysisPaper.paper_id == Paper.id).filter(
        AnalysisPaper.analysis_id == analysis.id
    ).all()
    return _apply_footnotes(analysis.report_md, papers)


def _serialize(db: Session, analysis: Analysis) -> dict:
    subfield = db.get(Subfield, analysis.subfield_id)
    field = db.get(Field, subfield.field_id)

    report_md, references = _footnoted_report(db, analysis)

    # 보고서 화면의 이동(목록으로 · 이전/다음 연도)에 필요한 최소 정보. 연도 목록은
    # 이 세부기술에 analyses 행이 있는 연도 전부다 — 미완료 연도로 가면 그 화면이
    # 진행 상태를 보여주므로 status로 거르지 않는다.
    years = [
        y for (y,) in db.query(Analysis.year)
        .filter(Analysis.subfield_id == subfield.id)
        .order_by(Analysis.year)
        .all()
    ]

    return {
        "id": analysis.id,
        "field_id": field.id,
        "field_name": field.name,
        "subfield_id": subfield.id,
        "subfield_name": subfield.name,
        "years": years,
        "year": analysis.year,
        "status": analysis.status,
        "status_label": STEP_LABELS.get(analysis.status, analysis.status),
        "report_md": report_md,
        "references": references,
        "stats": analysis.stats_json,
        "searched_count": analysis.searched_count,
        "analyzed_count": analysis.analyzed_count,
        "snapshot_at": analysis.snapshot_at.isoformat() if analysis.snapshot_at else None,
        "error": analysis.error,
    }


@router.get("/fields")
def list_fields(db: Session = Depends(get_db)):
    fields = db.query(Field).order_by(Field.order_no).all()
    # 분야마다 따로 조회하면 랜딩 페이지 한 번에 11번 질의가 나간다 — 한 번에 읽어 묶는다.
    subfields_by_field: dict[int, list] = {}
    for s in db.query(Subfield).all():
        subfields_by_field.setdefault(s.field_id, []).append(s)

    # 랜딩 화면의 진행 파이("세부기술 4개 중 3개 분석됨")용. 여기서도 분야마다 따로
    # 세면 11번 질의가 되므로 당해연도 done 건수를 한 번에 읽어 분야별로 묶는다.
    # 대상 연도는 서버의 "올해"다 — 사용자가 고르는 값이 아니라 "지금 기준 최신
    # 연도의 진행 상황"을 보여주는 것이 이 화면의 목적이다.
    current_year = datetime.now(timezone.utc).year
    done_by_field = dict(
        db.query(Subfield.field_id, func.count(Analysis.id))
        .join(Analysis, Analysis.subfield_id == Subfield.id)
        .filter(
            Analysis.year == current_year,
            Analysis.status == "done",
            Analysis.report_md.isnot(None),
            # 파이의 분모가 활성 세부기술 수이므로 분자도 같은 모집단이어야 한다 —
            # 분석을 마친 세부기술을 나중에 비활성화하면 done이 분모를 넘어선다.
            Subfield.active.is_(True),
        )
        .group_by(Subfield.field_id)
        .all()
    )

    return [
        {
            "id": f.id,
            "name": f.name,
            "slug": f.slug,
            "subfields": [
                {"id": s.id, "name": s.name, "active": s.active}
                for s in subfields_by_field.get(f.id, [])
            ],
            "current_year": current_year,
            "current_year_done": done_by_field.get(f.id, 0),
        }
        for f in fields
    ]


@router.get("/fields/{field_id}/years")
def field_years(field_id: int, db: Session = Depends(get_db)):
    """이 분야에서 보고서가 존재하는 연도 목록."""
    # 연도별로 세기만 하므로 두 컬럼이면 충분하다 — 전체 행을 실으면 report_md(건당
    # 12KB 규모)와 stats_json까지 읽어온다.
    rows = db.query(Analysis.year, Analysis.status).filter(
        Analysis.subfield_id.in_(
            db.query(Subfield.id).filter(Subfield.field_id == field_id)
        )
    ).all()

    by_year: dict[int, dict] = {}
    for row in rows:
        entry = by_year.setdefault(row.year, {"year": row.year, "subfield_count": 0, "done_count": 0})
        entry["subfield_count"] += 1
        if row.status == "done":
            entry["done_count"] += 1
    return sorted(by_year.values(), key=lambda e: e["year"], reverse=True)


@router.get("/fields/{field_id}/summary")
def field_summary(field_id: int, year: int, db: Session = Depends(get_db)):
    """세부기술별 논문 분포 — 대분류 레벨에서 세부기술들을 비교할 때만 의미가 있다.

    analyses 행이 없는 세부기술도 analysis_id=null / status="미실행"으로 포함한다.
    빠뜨리면 분포가 왜곡된다.
    """
    field = db.get(Field, field_id)
    if not field:
        raise HTTPException(status_code=404, detail="분야를 찾을 수 없습니다.")

    subfields = db.query(Subfield).filter(Subfield.field_id == field_id).order_by(Subfield.name).all()
    # 여기서 읽는 건 다섯 컬럼뿐이다 — 전체 행을 실으면 report_md(건당 12KB 규모)와
    # stats_json까지 딸려온다(dashboard·field_years와 같은 이유).
    analyses_by_subfield = {
        a.subfield_id: a
        for a in db.query(
            Analysis.id, Analysis.subfield_id, Analysis.status,
            Analysis.searched_count, Analysis.analyzed_count,
        ).filter(
            Analysis.subfield_id.in_([s.id for s in subfields]), Analysis.year == year
        )
    }

    rows = []
    total_searched = total_analyzed = 0
    for s in subfields:
        a = analyses_by_subfield.get(s.id)
        searched = a.searched_count if a else 0
        analyzed = a.analyzed_count if a else 0
        total_searched += searched
        total_analyzed += analyzed
        rows.append({
            "subfield_id": s.id,
            "subfield_name": s.name,
            "analysis_id": a.id if a else None,
            "status": a.status if a else "미실행",
            "status_label": STEP_LABELS.get(a.status, a.status) if a else "미실행",
            "searched_count": searched,
            "analyzed_count": analyzed,
        })

    return {
        "field_name": field.name,
        "year": year,
        "subfields": rows,
        "total_searched": total_searched,
        "total_analyzed": total_analyzed,
    }


def _done_report_count(db: Session, field_id: int, year: int) -> int:
    """지금 완성돼 있는 세부기술 보고서 수. 생성 시점의 source_count와 비교해
    "재생성이 필요한가"(stale)를 판정한다. report_md는 건당 12KB라 count로만 읽는다."""
    return (
        db.query(Analysis.id)
        .join(Subfield, Analysis.subfield_id == Subfield.id)
        .filter(
            Subfield.field_id == field_id,
            Analysis.year == year,
            Analysis.status == "done",
            Analysis.report_md.isnot(None),
        )
        .count()
    )


@router.get("/fields/{field_id}/report")
def field_report(field_id: int, year: int, db: Session = Depends(get_db)):
    """대분류 보고서 조회. 생성은 관리자만 할 수 있고(POST /api/admin/fields/{id}/report)
    여기서는 캐시된 결과만 읽는다."""
    row = (
        db.query(FieldReport)
        .filter(FieldReport.field_id == field_id, FieldReport.year == year)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="분야 보고서가 아직 생성되지 않았습니다.")

    current = _done_report_count(db, field_id, year)
    return {
        "field_id": row.field_id,
        "year": row.year,
        # pending/failed도 그대로 내려준다 — 처음 생성 중이면 report_md는 빈 문자열,
        # 재생성 중이면 이전 본문이 담겨 있어 화면이 옛 보고서를 보여주며 폴링한다.
        "status": row.status,
        "error": row.error,
        "report_md": row.report_md,
        "source_count": row.source_count,
        "current_count": current,
        "stale": current != row.source_count,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
    }


@router.get("/fields/{field_id}/roadmap-check")
def roadmap_check(field_id: int, year: int, db: Session = Depends(get_db)):
    """로드맵 이행 점검 보고서 조회. 캐시된 결과만 읽는다.

    로드맵 원문 자체는 내려주지 않는다 — 비공개 판본일 수 있고, 화면에 필요한 건
    점검 결과이지 원문이 아니다.
    """
    row = (
        db.query(RoadmapCheck)
        .filter(RoadmapCheck.field_id == field_id, RoadmapCheck.year == year)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="로드맵 점검 보고서가 아직 생성되지 않았습니다.")

    current = _done_report_count(db, field_id, year)
    roadmap = db.query(Roadmap).filter(Roadmap.field_id == field_id).one_or_none()
    return {
        "field_id": row.field_id,
        "year": row.year,
        "status": row.status,
        "error": row.error,
        "report_md": row.report_md,
        "source_count": row.source_count,
        "current_count": current,
        "goal_count": row.goal_count,
        "checked_count": row.checked_count,
        # 전수 점검이 깨진 채로 저장된 보고서 — 빠진 목표가 있다는 뜻이라
        # "빠짐없이 점검했다"로 읽히면 안 된다.
        "incomplete": row.checked_count != row.goal_count,
        "roadmap_version": row.roadmap_version,
        # 세부기술 보고서가 늘었거나 로드맵 판본이 바뀌면 재생성 대상이다.
        "stale": current != row.source_count
        or (roadmap is not None and roadmap.version_label != row.roadmap_version),
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
    }


@router.get("/fields/{field_id}/subfield-reports")
def subfield_reports(field_id: int, year: int, db: Session = Depends(get_db)):
    """분야 종합 보고서 전용 페이지의 "세부기술 보고서 포함" 토글용 — 이 분야·연도에서
    완성된 세부기술 보고서를 이어붙이기 좋게 목록으로 내려준다.

    세부기술 보고서 화면(get_by_subfield_year)과 똑같이 각주 치환을 적용한다 —
    빼면 부록에 논문 제목이 full name 그대로 노출된다(_footnoted_report). 참고문헌도
    함께 내려 화면이 [n] 각주 아래 목록을 붙일 수 있게 한다.
    """
    subfields = (
        db.query(Subfield)
        .filter(Subfield.field_id == field_id)
        .order_by(Subfield.name)
        .all()
    )
    reports = []
    for sf in subfields:
        analysis = (
            db.query(Analysis)
            .filter(
                Analysis.subfield_id == sf.id,
                Analysis.year == year,
                Analysis.status == "done",
                Analysis.report_md.isnot(None),
            )
            .first()
        )
        if analysis is None or not analysis.report_md:
            continue
        md, references = _footnoted_report(db, analysis)
        reports.append({"name": sf.name, "report_md": md, "references": references})
    return {"field_id": field_id, "year": year, "reports": reports}


@router.get("/site-info")
def site_info(request: Request):
    """푸터에 표시할 도메인·버전. 버전은 프론트 package.json이 단일 출처이므로
    프론트는 이 값 대신 빌드 타임에 주입된 자기 자신의 버전을 쓴다 — 여기서는
    FastAPI 앱에 이미 있는 버전 속성을 그대로 노출한다."""
    return {"domain": settings.site_domain, "version": request.app.version}


@router.get("/visitors")
def get_visitors(db: Session = Depends(get_db)):
    return visitors_service.visitor_stats(db)


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: int, db: Session = Depends(get_db)):
    analysis = db.get(Analysis, analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="분석 결과를 찾을 수 없습니다.")
    return _serialize(db, analysis)


@router.get("/subfields/{subfield_id}/analyses/{year}")
def get_by_subfield_year(subfield_id: int, year: int, db: Session = Depends(get_db)):
    analysis = db.query(Analysis).filter(
        Analysis.subfield_id == subfield_id, Analysis.year == year
    ).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="분석 결과를 찾을 수 없습니다.")
    return _serialize(db, analysis)
