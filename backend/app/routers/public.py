import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.clients._html import strip_html
from app.config import settings
from app.database import get_db
from app.models.analysis import Analysis, AnalysisPaper
from app.models.field import Field, Subfield
from app.models.paper import Paper
from app.services import visitors as visitors_service
from app.services.runner import STEP_LABELS

router = APIRouter(prefix="/api", tags=["public"])

# 한 단계 중첩까지 허용한다: 인용문 안에 "TrioN (3N0C)"처럼 논문 제목 자체가 괄호를
# 품는 경우, 바깥 인용 괄호 전체를 잡아야지 안쪽 괄호만 잡으면 안 된다(그러면 바깥 인용
# 전체가 미치환으로 남는다). `(?:[^()]|\([^()]*\))*` — 괄호가 아닌 문자이거나, 괄호가 없는
# 내용의 중첩 괄호 한 겹. 두 단계 이상 중첩은 다루지 않는다 — 실제 인용 데이터는 한 겹이면
# 충분하고, 그 이상을 지원하려면 재귀적 괄호 파서가 필요해 복잡도만 커진다.
_PAREN_RE = re.compile(r"\(((?:[^()]|\([^()]*\))+)\)")

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


def _apply_footnotes(report_md: str | None, papers: list[Paper]) -> tuple[str | None, list[dict]]:
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
    candidates: list[tuple[Paper, str]] = sorted(
        ((p, _footnote_key(p.title)) for p in papers if p.title and p.title.strip()),
        key=lambda pc: len(pc[1]),
        reverse=True,
    )

    numbers: dict[int, int] = {}  # paper.id -> 각주 번호
    references: list[dict] = []

    def repl(m: re.Match) -> str:
        content_key = _footnote_key(m.group(1))
        matched: Paper | None = None
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

    return _PAREN_RE.sub(repl, report_md), references


def _serialize(db: Session, analysis: Analysis) -> dict:
    subfield = db.get(Subfield, analysis.subfield_id)
    field = db.get(Field, subfield.field_id)

    paper_ids = [
        row.paper_id
        for row in db.query(AnalysisPaper.paper_id).filter(AnalysisPaper.analysis_id == analysis.id)
    ]
    papers = db.query(Paper).filter(Paper.id.in_(paper_ids)).all() if paper_ids else []
    report_md, references = _apply_footnotes(analysis.report_md, papers)

    return {
        "id": analysis.id,
        "field_name": field.name,
        "subfield_name": subfield.name,
        "year": analysis.year,
        "status": analysis.status,
        "status_label": STEP_LABELS.get(analysis.status, analysis.status),
        "report_md": report_md,
        "references": references,
        "stats": analysis.stats_json,
        "searched_count": analysis.searched_count,
        "analyzed_count": analysis.analyzed_count,
        "sampled": analysis.sampled,
        "snapshot_at": analysis.snapshot_at.isoformat() if analysis.snapshot_at else None,
        "error": analysis.error,
    }


@router.get("/fields")
def list_fields(db: Session = Depends(get_db)):
    fields = db.query(Field).order_by(Field.order_no).all()
    return [
        {
            "id": f.id,
            "name": f.name,
            "slug": f.slug,
            "subfields": [
                {"id": s.id, "name": s.name, "active": s.active}
                for s in db.query(Subfield).filter(Subfield.field_id == f.id).all()
            ],
        }
        for f in fields
    ]


@router.get("/fields/{field_id}/years")
def field_years(field_id: int, db: Session = Depends(get_db)):
    """이 분야에서 보고서가 존재하는 연도 목록."""
    subfield_ids = [s.id for s in db.query(Subfield).filter(Subfield.field_id == field_id)]
    rows = db.query(Analysis).filter(Analysis.subfield_id.in_(subfield_ids)).all()

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
    analyses_by_subfield = {
        a.subfield_id: a
        for a in db.query(Analysis).filter(
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
