import hashlib
import logging
from typing import NamedTuple

import httpx
from sqlalchemy.orm import Session

import asyncio

from app.clients import elsevier, kci, openalex
from app.config import settings
from app.models.field import Subfield
from app.models.paper import Paper
from app.services import budget

logger = logging.getLogger(__name__)


class SearchResult(NamedTuple):
    papers: list[dict]  # OpenAlex + KCI 병합 결과 (max_papers_per_analysis로 잘려 있을 수 있음)
    total_count: int  # OpenAlex가 보고한 잘리기 전 전체 건수 — 하드 가드는 이 값으로 판단해야 한다.


def query_hash(subfield: Subfield, year_from: int, year_to: int, country: str = "KR") -> str:
    """검색식·연도 범위·국가가 바뀌면 해시가 달라져 해당 분석이 '갱신 필요'로 표시된다.

    국가를 빼면 KR 분석과 US 분석이 서로를 최신으로 착각한다."""
    raw = f"{subfield.query}\x00{subfield.query_kci or ''}\x00{year_from}-{year_to}\x00{country}"
    return hashlib.sha256(raw.encode()).hexdigest()


_LONGER_WINS = ("abstract", "title")  # 둘 다 있으면 더 긴 쪽
_FIRST_NONEMPTY_WINS = ("journal", "doi", "year")  # 둘 다 있으면 먼저 온 쪽
_LONGER_LIST_WINS = ("authors", "institutions", "countries", "lead_countries")  # 원소 더 많은 쪽


def combine_source(existing_source: str | None, new_source: str | None) -> str:
    """I10: 어느 소스에서 발견됐는지를 잃지 않게 소스 라벨을 합성한다.

    양쪽에서 같은 논문이 발견되면 "both"로 남긴다. Paper.source는 단일 문자열
    컬럼이라 새 컬럼 없이 값의 의미를 "양쪽에서 발견됨"까지 확장하는 쪽을 택했다 —
    stats.by_source가 openalex-only / kci-only / both를 그대로 Counter로 구분할 수 있다.
    한쪽만 다시 검색돼도(예: 재수집 시 KCI가 이번엔 안 걸림) 기존 "both" 판정이
    깎이면 안 되므로 이미 both면 그대로 both를 유지한다.
    """
    if not existing_source:
        return new_source or ""
    if not new_source or new_source == existing_source:
        return existing_source
    return "both"


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
            existing["source"] = combine_source(existing.get("source"), paper.get("source"))
            # paper_key: 먼저 온 소스(existing)를 그대로 유지한다(식별자라 병합 대상 아님).
    return list(merged.values())


async def _fill_missing_abstracts(
    db: Session, papers: list[dict], *, client: httpx.AsyncClient
) -> int:
    """abstract가 빈 Elsevier 논문의 초록을 회수해 dict에 채운다. 채운 건수를 돌려준다.

    OpenAlex 결측 초록의 66.8%가 Elsevier 게재분이고(실측) ScienceDirect Article
    Retrieval이 무료 키로 88%를 돌려준다. 여기서 dict를 채우면 하류(upsert_papers →
    mapper.pending_papers → stats)는 한 줄도 바뀌지 않는다.

    대상은 세 조건을 **모두** 만족할 때만이다:
      ① abstract가 비어 있고 ② DOI가 10.1016/으로 시작하며 ③ DB에도 초록이 없다.

    ③이 없으면 이미 회수해 저장해 둔 논문을 매달 다시 받아온다 — OpenAlex는 같은 논문을
    계속 초록 없이 돌려주기 때문이다(KR 기준 연 36,000콜 낭비).

    실패는 전부 흡수한다. 보강 단계라 빠져도 결과가 틀리지 않고 예전만큼만 분석될 뿐이며,
    빠진 만큼은 stats의 no_abstract_count가 드러낸다(검색 소스인 KCI와 정반대 정책).

    ponytail: 실패한 논문에 "시도했음" 표시를 남기지 않는다. 영구 실패는 대상의 약
    12%(KR 기준 연 360건)라 매달 재시도해도 주당 5만 건 한도의 0.2%이고, 오히려
    Elsevier가 나중에 초록을 채우면 자동으로 회수되는 이득이 있다. 컬럼을 두면
    마이그레이션과 "언제 만료시킬 것인가" 정책이 새로 생긴다.
    """
    if not settings.elsevier_api_key:
        return 0

    candidates = [
        p for p in papers
        if not p.get("abstract")
        and (p.get("doi") or "").startswith(elsevier.ELSEVIER_DOI_PREFIX)
    ]
    if not candidates:
        return 0

    keys = [p["paper_key"] for p in candidates]
    stored = {
        key for (key,) in db.query(Paper.paper_key).filter(
            Paper.paper_key.in_(keys), Paper.abstract != ""
        )
    }
    targets = [p for p in candidates if p["paper_key"] not in stored]
    if not targets:
        return 0

    semaphore = asyncio.Semaphore(max(1, settings.elsevier_concurrency))

    async def one(paper: dict) -> bool:
        async with semaphore:
            try:
                text = await elsevier.fetch_abstract(paper["doi"], client=client)
            except Exception as e:
                # 클라이언트는 예외를 던지지 않기로 돼 있지만, 그 계약이 깨져도
                # 분석을 멈추지 않는다.
                logger.debug("[초록회수] %s 실패: %s", paper.get("doi"), e)
                return False
            if not text:
                return False
            paper["abstract"] = text
            return True

    results = await asyncio.gather(*(one(p) for p in targets))
    filled = sum(results)
    logger.info(
        "[초록회수] 대상 %d건(전체 %d, DB 기보유 제외 %d) → 회수 %d건",
        len(targets), len(papers), len(candidates) - len(targets), filled,
    )
    return filled


async def collect(
    db: Session,
    subfield: Subfield,
    year_from: int,
    year_to: int,
    *,
    client: httpx.AsyncClient,
    country: str = "KR",
) -> SearchResult:
    """OpenAlex + KCI 검색 후 병합. OpenAlex 비용은 실측해 예산에 기록한다.

    total_count는 OpenAlex가 보고한 잘리기 전 전체 건수다 — papers는 항상
    max_papers_per_analysis 이하로 잘려 있으므로, 과광범위 검색식을 걸러내는
    하드 가드는 반드시 이 값을 기준으로 판단해야 한다(C1).
    """
    # 여기서는 count_only를 유지한다 — 페이징에 실제로 돈을 쓰기 전에 예산 게이트를
    # 통과시키는 게 목적이라, search()의 total_count를 보려고 먼저 페이징을 시작할 수 없다.
    count, count_cost = await openalex.count_only(
        subfield.query, year_from, year_to, client=client, country=country
    )
    # 페이지 단가는 설정값을 쓴다 — count_cost는 per-page=1 탐색 요청의 실측 비용이라
    # 페이지 단가로 곱하면 단위가 어긋나고, meta.cost_usd가 비어 0으로 오면 견적이
    # 통째로 0이 되어 게이트가 무력화된다(대량 크롤이 그대로 통과한다).
    budget.check_budget(
        db, count_cost + openalex.estimate_pages(count) * settings.openalex_search_cost_usd
    )

    try:
        oa = await openalex.search(
            subfield.query, year_from, year_to, client=client,
            limit=settings.max_papers_per_analysis, country=country,
        )
    except Exception as e:
        # I6: 페이지 중간에 실패해도 이미 발생한 비용은 예산에 반영해야 한다 — 그러지
        # 않으면 spent_today가 실제 소비를 못 따라가 예산 게이트가 무력화된다.
        partial_cost = getattr(e, "cost_usd", 0.0)
        budget.record_usage(db, count_cost + partial_cost, None)
        raise
    budget.record_usage(db, count_cost + oa.cost_usd, oa.remaining)

    # KCI는 한국학술지 전용이다. 타국 분석에서 부르면 (a) 만료된 키 때문에 무의미하게
    # failed되고 (b) KR에만 국내지가 섞여 소스가 비대칭이 된다 — 국가 비교에서 KR
    # 논문 수만 구조적으로 부풀린다.
    kci_papers: list[dict] = []
    if country == "KR":
        kci_papers = await kci.search(
            subfield.kci_query(), year_from, year_to,
            client=client, limit=settings.max_papers_per_analysis,
        )

    merged = merge_papers(oa.papers, kci_papers)
    # OpenAlex에 초록이 없는 Elsevier 논문을 여기서 채운다 — 하류(upsert_papers →
    # pending_papers → stats)는 dict의 abstract만 보므로 바뀌는 곳이 없다.
    await _fill_missing_abstracts(db, merged, client=client)
    logger.info(
        "[검색] %s %d-%d: OpenAlex %d(전체 %d) + KCI %d → 병합 %d",
        subfield.name, year_from, year_to, len(oa.papers), oa.total_count, len(kci_papers), len(merged),
    )
    return SearchResult(papers=merged, total_count=oa.total_count)


_FIELDS = ("title", "abstract", "year", "journal", "doi", "authors", "institutions",
           "countries", "lead_countries", "citations", "source")
_JSON_MAP = {"authors": "authors_json", "institutions": "institutions_json",
             "countries": "countries_json", "lead_countries": "lead_countries_json"}


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
            if field == "source":
                # I10: 단일 collect() 호출 안의 병합(merge_papers)뿐 아니라, 서로 다른
                # 시점의 재검색에 걸쳐서도 "양쪽에서 발견됨" 판정이 유지돼야 한다 —
                # 그러지 않으면 이번 회차에 KCI가 안 걸렸다는 이유로 both가 openalex로
                # 조용히 되돌아간다.
                new_value = combine_source(row.source, new_value)
            if new_value or not getattr(row, attr, None):
                setattr(row, attr, new_value)
        rows.append(row)

    db.commit()
    return rows
