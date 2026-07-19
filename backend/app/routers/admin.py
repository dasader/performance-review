import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.clients import kci, openalex
from app.config import settings
from app.database import get_db
from app.deps import require_admin
from app.models.analysis import Analysis
from app.models.field import Field, Subfield
from app.services import budget, runner

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class SubfieldIn(BaseModel):
    field_id: int
    name: str
    query: str
    query_kci: str | None = None
    active: bool = True


class PreviewIn(BaseModel):
    subfield_id: int
    year_from: int
    year_to: int


class RunIn(BaseModel):
    subfield_ids: list[int]
    year_from: int
    year_to: int
    force: bool = False


@router.post("/auth")
def auth():
    """키 검증 전용 — 프론트가 입력값을 확인할 때 쓴다."""
    return {"ok": True}


@router.get("/subfields")
def list_subfields(db: Session = Depends(get_db)):
    return [
        {"id": s.id, "field_id": s.field_id, "name": s.name, "query": s.query,
         "query_kci": s.query_kci, "active": s.active}
        for s in db.query(Subfield).order_by(Subfield.field_id, Subfield.name).all()
    ]


@router.post("/subfields")
def create_subfield(payload: SubfieldIn, db: Session = Depends(get_db)):
    if not db.get(Field, payload.field_id):
        raise HTTPException(status_code=404, detail="분야를 찾을 수 없습니다.")
    row = Subfield(**payload.model_dump())
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name}


@router.put("/subfields/{subfield_id}")
def update_subfield(subfield_id: int, payload: SubfieldIn, db: Session = Depends(get_db)):
    row = db.get(Subfield, subfield_id)
    if not row:
        raise HTTPException(status_code=404, detail="세부기술을 찾을 수 없습니다.")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    db.commit()
    return {"id": row.id, "name": row.name}


@router.delete("/subfields/{subfield_id}")
def delete_subfield(subfield_id: int, db: Session = Depends(get_db)):
    row = db.get(Subfield, subfield_id)
    if not row:
        raise HTTPException(status_code=404, detail="세부기술을 찾을 수 없습니다.")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/preview")
async def preview(payload: PreviewIn, db: Session = Depends(get_db)):
    """검색만 실행해 건수·샘플·예상 비용을 보여준다. LLM은 호출하지 않는다."""
    subfield = db.get(Subfield, payload.subfield_id)
    if not subfield:
        raise HTTPException(status_code=404, detail="세부기술을 찾을 수 없습니다.")

    async with httpx.AsyncClient() as client:
        count, cost = await openalex.count_only(
            subfield.query, payload.year_from, payload.year_to, client=client
        )
        sample = await openalex.search(
            subfield.query, payload.year_from, payload.year_to, client=client, limit=20
        )
        kci_papers = await kci.search(
            subfield.kci_query(), payload.year_from, payload.year_to, client=client, limit=20
        )

    budget.record_usage(db, cost + sample.cost_usd, sample.remaining)
    pages = max(1, -(-min(count, settings.max_papers_per_analysis) // settings.openalex_per_page))

    return {
        "openalex_count": count,
        "kci_count": len(kci_papers),
        "samples": [
            {"title": p["title"], "year": p["year"], "journal": p["journal"],
             "has_abstract": bool(p["abstract"])}
            for p in sample.papers[:20]
        ],
        "estimated_pages": pages,
        "estimated_cost_usd": round(pages * settings.openalex_search_cost_usd, 4),
        "budget_spent": round(budget.spent_today(db), 4),
        "budget_limit": settings.openalex_daily_budget_usd,
        "over_limit": count > settings.max_papers_per_analysis,
        "max_papers": settings.max_papers_per_analysis,
    }


@router.post("/run")
def run(payload: RunIn, db: Session = Depends(get_db)):
    queued, blocked = [], []
    for subfield_id in payload.subfield_ids:
        subfield = db.get(Subfield, subfield_id)
        if not subfield:
            blocked.append({"subfield_id": subfield_id, "reason": "세부기술 없음"})
            continue
        for analysis in runner.enqueue(
            db, subfield, payload.year_from, payload.year_to, force=payload.force
        ):
            queued.append(analysis.id)
    return {"queued": queued, "blocked": blocked}


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    """세부기술 × 연도 격자. 검색식이 바뀐 항목은 stale=True로 표시된다."""
    rows = []
    for subfield in db.query(Subfield).order_by(Subfield.field_id, Subfield.name).all():
        analyses = db.query(Analysis).filter(Analysis.subfield_id == subfield.id).all()
        rows.append({
            "subfield_id": subfield.id,
            "subfield_name": subfield.name,
            "field_id": subfield.field_id,
            "years": [
                {
                    "analysis_id": a.id,
                    "year": a.year,
                    "status": a.status,
                    "status_label": runner.STEP_LABELS.get(a.status, a.status),
                    "searched_count": a.searched_count,
                    "analyzed_count": a.analyzed_count,
                    "snapshot_at": a.snapshot_at.isoformat() if a.snapshot_at else None,
                    "stale": runner.is_stale(db, a, subfield),
                    "error": a.error,
                }
                for a in sorted(analyses, key=lambda x: x.year, reverse=True)
            ],
        })
    return {
        "rows": rows,
        "budget_spent": round(budget.spent_today(db), 4),
        "budget_limit": settings.openalex_daily_budget_usd,
        "default_year_range": settings.default_year_range,
    }


@router.post("/analyses/{analysis_id}/retry")
def retry(analysis_id: int, db: Session = Depends(get_db)):
    analysis = db.get(Analysis, analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="분석을 찾을 수 없습니다.")
    analysis.status = "pending"
    analysis.error = None
    analysis.batch_job_id = None
    db.commit()
    return {"ok": True, "id": analysis.id}
