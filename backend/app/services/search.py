import hashlib
import logging
from typing import NamedTuple

import httpx
from sqlalchemy.orm import Session

from app.clients import kci, openalex
from app.config import settings
from app.models.field import Subfield
from app.models.paper import Paper
from app.services import budget

logger = logging.getLogger(__name__)


class SearchResult(NamedTuple):
    papers: list[dict]  # OpenAlex + KCI 병합 결과 (max_papers_per_analysis로 잘려 있을 수 있음)
    total_count: int  # OpenAlex가 보고한 잘리기 전 전체 건수 — 하드 가드는 이 값으로 판단해야 한다.


def query_hash(subfield: Subfield, year_from: int, year_to: int) -> str:
    """검색식이나 연도 범위가 바뀌면 해시가 달라져 해당 분석이 '갱신 필요'로 표시된다."""
    raw = f"{subfield.query}\x00{subfield.query_kci or ''}\x00{year_from}-{year_to}"
    return hashlib.sha256(raw.encode()).hexdigest()


_LONGER_WINS = ("abstract", "title")  # 둘 다 있으면 더 긴 쪽
_FIRST_NONEMPTY_WINS = ("journal", "doi", "year")  # 둘 다 있으면 먼저 온 쪽
_LONGER_LIST_WINS = ("authors", "institutions", "countries")  # 원소 더 많은 쪽


def merge_papers(*sources: list[dict]) -> list[dict]:
    """paper_key 기준으로 필드 단위 병합한다 (레코드 통째 선택이 아님).

    OpenAlex는 abstract가 자주 빠지고 KCI는 authors/institutions가 항상 비어
    있으므로, 레코드 하나를 고르면 다른 소스의 값을 통째로 버리게 된다.
    각 필드마다 비어 있지 않은 값을 채택하고 소스 간 우열은 필드별 규칙을 따른다.
    """
    merged: dict[str, dict] = {}
    for source in sources:
        for paper in source:
            key = paper["paper_key"]
            if key not in merged:
                merged[key] = dict(paper)
                continue
            existing = merged[key]
            for field in _LONGER_WINS:
                new_val, old_val = paper.get(field) or "", existing.get(field) or ""
                if new_val and len(new_val) > len(old_val):
                    existing[field] = paper[field]
            for field in _FIRST_NONEMPTY_WINS:
                if not existing.get(field) and paper.get(field):
                    existing[field] = paper[field]
            for field in _LONGER_LIST_WINS:
                new_list, old_list = paper.get(field) or [], existing.get(field) or []
                if len(new_list) > len(old_list):
                    existing[field] = paper[field]
            existing["citations"] = max(existing.get("citations") or 0, paper.get("citations") or 0)
            existing["korea_flag"] = bool(existing.get("korea_flag")) or bool(paper.get("korea_flag"))
            # source, paper_key: 먼저 온 소스(existing)를 그대로 유지한다.
    return list(merged.values())


async def collect(
    db: Session,
    subfield: Subfield,
    year_from: int,
    year_to: int,
    *,
    client: httpx.AsyncClient,
) -> SearchResult:
    """OpenAlex + KCI 검색 후 병합. OpenAlex 비용은 실측해 예산에 기록한다.

    total_count는 OpenAlex가 보고한 잘리기 전 전체 건수다 — papers는 항상
    max_papers_per_analysis 이하로 잘려 있으므로, 과광범위 검색식을 걸러내는
    하드 가드는 반드시 이 값을 기준으로 판단해야 한다(C1).
    """
    count, count_cost = await openalex.count_only(subfield.query, year_from, year_to, client=client)
    pages = max(1, -(-min(count, settings.max_papers_per_analysis) // settings.openalex_per_page))
    budget.check_budget(db, count_cost + pages * count_cost)

    try:
        oa = await openalex.search(
            subfield.query, year_from, year_to, client=client, limit=settings.max_papers_per_analysis
        )
    except Exception as e:
        # I6: 페이지 중간에 실패해도 이미 발생한 비용은 예산에 반영해야 한다 — 그러지
        # 않으면 spent_today가 실제 소비를 못 따라가 예산 게이트가 무력화된다.
        partial_cost = getattr(e, "cost_usd", 0.0)
        budget.record_usage(db, count_cost + partial_cost, None)
        raise
    budget.record_usage(db, count_cost + oa.cost_usd, oa.remaining)

    kci_papers = await kci.search(
        subfield.kci_query(), year_from, year_to,
        client=client, limit=settings.max_papers_per_analysis,
    )

    merged = merge_papers(oa.papers, kci_papers)
    logger.info(
        "[검색] %s %d-%d: OpenAlex %d(전체 %d) + KCI %d → 병합 %d",
        subfield.name, year_from, year_to, len(oa.papers), oa.total_count, len(kci_papers), len(merged),
    )
    return SearchResult(papers=merged, total_count=oa.total_count)


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
