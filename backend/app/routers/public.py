import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.analysis import Analysis, AnalysisPaper
from app.models.field import Field, Subfield
from app.models.paper import Paper
from app.services import visitors as visitors_service
from app.services.runner import STEP_LABELS

router = APIRouter(prefix="/api", tags=["public"])

_PAREN_RE = re.compile(r"\(([^()]+)\)")


def _normalize_ws(s: str) -> str:
    return " ".join(s.split())


def _apply_footnotes(report_md: str | None, papers: list[Paper]) -> tuple[str | None, list[dict]]:
    """report_md 안에서 괄호로 인용된 논문 제목을 각주 번호로 치환한다(읽기 시점 후처리,
    LLM 재호출 없음). 매칭은 보수적으로: 괄호 안 텍스트가 공백 정규화 후 논문 제목과
    정확히 같을 때만 치환한다. 못 찾은 제목은 원문 그대로 둔다.

    각주는 `[\\[1\\]](#ref-1)` 형태의 마크다운 링크로 치환된다 — 대괄호를 이스케이프해
    렌더링 시 보이는 텍스트는 그대로 "[1]"이면서 "#ref-1"(참고문헌 항목)로 이동하는 링크가
    된다. 첫 인용 지점에 돌아올 앵커(id="cite-1")를 붙이는 책임은 프론트(Report.tsx)에
    있다 — 같은 논문이 여러 번 인용될 수 있어 "첫 인용 지점"을 여기(파이썬)에서 가리려면
    별도 상태 추적이 필요하지만, 프론트는 이미 각 인용 링크를 렌더링하는 시점에 있으므로
    처음 본 #ref-n을 추적하는 쪽이 더 단순하다(추가 마크업/원시 HTML도 필요 없음)."""
    if not report_md:
        return report_md, []

    title_by_norm: dict[str, Paper] = {}
    for p in papers:
        key = _normalize_ws(p.title or "")
        if key and key not in title_by_norm:
            title_by_norm[key] = p

    numbers: dict[str, int] = {}
    references: list[dict] = []

    def repl(m: re.Match) -> str:
        norm = _normalize_ws(m.group(1))
        paper = title_by_norm.get(norm)
        if paper is None:
            return m.group(0)
        n = numbers.get(norm)
        if n is None:
            n = len(references) + 1
            numbers[norm] = n
            references.append({
                "n": n,
                "title": paper.title,
                "journal": paper.journal,
                "year": paper.year,
                "doi": paper.doi,
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
