import hashlib
import logging

import httpx
from sqlalchemy.orm import Session

from app.clients import kci, openalex
from app.config import settings
from app.models.field import Subfield
from app.models.paper import Paper
from app.services import budget

logger = logging.getLogger(__name__)


def query_hash(subfield: Subfield, year_from: int, year_to: int) -> str:
    """검색식이나 연도 범위가 바뀌면 해시가 달라져 해당 분석이 '갱신 필요'로 표시된다."""
    raw = f"{subfield.query}\x00{subfield.query_kci or ''}\x00{year_from}-{year_to}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _score(paper: dict) -> tuple:
    """병합 시 우선순위 — abstract 보유 > 저자 정보 보유 > 인용수."""
    return (bool(paper.get("abstract")), len(paper.get("authors") or []), paper.get("citations") or 0)


def merge_papers(*sources: list[dict]) -> list[dict]:
    """paper_key 기준 중복 제거. 같은 키면 정보가 더 채워진 레코드를 남긴다."""
    best: dict[str, dict] = {}
    for source in sources:
        for paper in source:
            key = paper["paper_key"]
            if key not in best or _score(paper) > _score(best[key]):
                best[key] = paper
    return list(best.values())


async def collect(
    db: Session,
    subfield: Subfield,
    year_from: int,
    year_to: int,
    *,
    client: httpx.AsyncClient,
) -> list[dict]:
    """OpenAlex + KCI 검색 후 병합. OpenAlex 비용은 실측해 예산에 기록한다."""
    count, count_cost = await openalex.count_only(subfield.query, year_from, year_to, client=client)
    pages = max(1, -(-min(count, settings.max_papers_per_analysis) // settings.openalex_per_page))
    budget.check_budget(db, count_cost + pages * count_cost)

    oa = await openalex.search(
        subfield.query, year_from, year_to, client=client, limit=settings.max_papers_per_analysis
    )
    budget.record_usage(db, count_cost + oa.cost_usd, oa.remaining)

    kci_papers = await kci.search(
        subfield.kci_query(), year_from, year_to,
        client=client, limit=settings.max_papers_per_analysis,
    )

    merged = merge_papers(oa.papers, kci_papers)
    logger.info(
        "[검색] %s %d-%d: OpenAlex %d + KCI %d → 병합 %d",
        subfield.name, year_from, year_to, len(oa.papers), len(kci_papers), len(merged),
    )
    return merged


_FIELDS = ("title", "abstract", "year", "journal", "doi", "authors", "institutions",
           "countries", "citations", "source", "korea_flag")
_JSON_MAP = {"authors": "authors_json", "institutions": "institutions_json",
             "countries": "countries_json"}


def upsert_papers(db: Session, papers: list[dict]) -> list[Paper]:
    """paper_key 기준 upsert. 기존 행은 값이 더 채워진 경우에만 덮어쓴다."""
    keys = [p["paper_key"] for p in papers]
    existing = {r.paper_key: r for r in db.query(Paper).filter(Paper.paper_key.in_(keys)).all()}

    rows: list[Paper] = []
    for paper in papers:
        row = existing.get(paper["paper_key"])
        if row is None:
            row = Paper(paper_key=paper["paper_key"])
            db.add(row)
            existing[paper["paper_key"]] = row
        for field in _FIELDS:
            attr = _JSON_MAP.get(field, field)
            new_value = paper.get(field)
            if new_value or not getattr(row, attr, None):
                setattr(row, attr, new_value)
        rows.append(row)

    db.commit()
    return rows
