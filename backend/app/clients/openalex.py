import logging
from typing import NamedTuple

import httpx

from app.clients._doi import strip_doi_prefix
from app.clients._html import strip_html
from app.clients._http import get_with_retry
from app.config import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.openalex.org/works"
SELECT = (
    "id,doi,title,publication_year,cited_by_count,"
    "abstract_inverted_index,primary_location,authorships"
)


class OpenAlexResult(NamedTuple):
    papers: list[dict]
    cost_usd: float
    remaining: str | None
    total_count: int


def reconstruct_abstract(inv_idx: dict[str, list[int]] | None) -> str:
    """OpenAlex는 abstract를 단어→위치 역색인으로 준다. 위치 순으로 되돌린다."""
    if not inv_idx:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inv_idx.items():
        for idx in idxs:
            positions[idx] = word
    if not positions:
        return ""
    return " ".join(positions[i] for i in sorted(positions))


def _parse_work(work: dict) -> dict:
    doi = strip_doi_prefix(work.get("doi"))
    oa_id = (work.get("id") or "").rsplit("/", 1)[-1]
    authorships = work.get("authorships") or []

    authors, institutions, countries = [], [], []
    lead_countries: list[str] = []
    for a in authorships:
        name = (a.get("author") or {}).get("display_name")
        if name:
            authors.append(name)
        for inst in a.get("institutions") or []:
            if inst.get("display_name"):
                institutions.append(inst["display_name"])
            code = inst.get("country_code")
            if code and code not in countries:
                countries.append(code)
            # is_corresponding이 없는 논문이 6~9% 있다 — 그때는 비워 두고 stats가
            # "주도 미상"으로 센다. 추측해 채우면 주도/참여 비율이 조용히 틀어진다.
            if code and a.get("is_corresponding") and code not in lead_countries:
                lead_countries.append(code)

    location = work.get("primary_location") or {}
    journal = (location.get("source") or {}).get("display_name")
    return {
        "paper_key": doi or f"openalex:{oa_id}",
        "title": strip_html(work.get("title") or ""),
        "abstract": strip_html(reconstruct_abstract(work.get("abstract_inverted_index"))),
        "year": work.get("publication_year"),
        "journal": strip_html(journal) if journal else journal,
        "doi": doi,
        "authors": authors,
        "institutions": institutions,
        "countries": countries,
        "lead_countries": lead_countries,
        "citations": int(work.get("cited_by_count") or 0),
        "source": "openalex",
    }


def _sanitize_query(query: str) -> str:
    """콤마(AND)와 파이프(OR)는 OpenAlex filter DSL의 절 구분자라 이스케이프가
    불가능하다 — 관리자 입력에 섞여 들어오면 검색식이 조용히 쪼개지므로 공백으로 치환한다."""
    return query.replace(",", " ").replace("|", " ")


def _filter_expr(query: str, year_from: int, year_to: int, country: str = "KR") -> str:
    """연도를 범위로 한 번에 건다 — 연도별 개별 조회 대비 콜수가 1/N이 된다.
    국가 필터를 서버측에 걸어 불필요한 페이지를 받지 않는다(추가 비용 0)."""
    return (
        f"title_and_abstract.search:{_sanitize_query(query)},"
        f"publication_year:{year_from}-{year_to},"
        f"authorships.institutions.country_code:{country}"
    )


def _base_params(query: str, year_from: int, year_to: int, country: str = "KR") -> dict:
    return {
        "filter": _filter_expr(query, year_from, year_to, country),
        "api_key": settings.openalex_api_key,
    }


def estimate_pages(count: int) -> int:
    """이 건수를 다 받으려면 cursor 페이징이 몇 콜 필요한지. OpenAlex는 요청 건당
    과금하므로 이 값이 곧 예상 비용의 배수다(미리보기 견적·예산 사전 게이트 공용)."""
    capped = min(count, settings.max_papers_per_analysis)
    return max(1, -(-capped // settings.openalex_per_page))


async def count_only(
    query: str, year_from: int, year_to: int, *, client: httpx.AsyncClient,
    country: str = "KR",
) -> tuple[int, float]:
    """검색 건수만 확인한다(미리보기·실행 전 견적용). per_page=1로 1콜."""
    params = {**_base_params(query, year_from, year_to, country), "per-page": 1, "select": "id"}
    response = await get_with_retry(
        API_URL, client=client, params=params, service_name="OpenAlex", context=query
    )
    data = response.json()
    meta = data.get("meta") or {}
    return int(meta.get("count") or 0), float(meta.get("cost_usd") or 0.0)


async def search(
    query: str, year_from: int, year_to: int, *, client: httpx.AsyncClient, limit: int,
    country: str = "KR",
) -> OpenAlexResult:
    """cursor 페이징으로 최대 `limit`건 수집. 비용과 잔여 헤더를 누적해 함께 반환한다.

    인용수 내림차순으로 정렬해 받는다. 상한(max_papers_per_analysis)에 걸려 잘릴 때
    **무엇이 남는지**를 정하기 위해서다 — OpenAlex 기본 정렬(relevance_score)은 텍스트
    유사도가 섞인 불투명한 점수라 국가 간 비교의 기준선으로 쓸 수 없다. cursor 페이징과
    병용되고 비용이 동일한 것, 당해연도도 정렬이 유의미한 것은 실측으로 확인했다.
    분석은 연도별로 따로 돌므로(enqueue) 정렬 대상이 항상 한 연도 안이라 연도 간
    인용 누적 차이가 개입하지 않는다."""
    papers: list[dict] = []
    cost = 0.0
    remaining: str | None = None
    total = 0
    cursor = "*"

    try:
        while cursor and len(papers) < limit:
            params = {
                **_base_params(query, year_from, year_to, country),
                "per-page": min(settings.openalex_per_page, limit - len(papers)),
                "select": SELECT,
                "cursor": cursor,
                "sort": "cited_by_count:desc",
            }
            response = await get_with_retry(
                API_URL, client=client, params=params, service_name="OpenAlex", context=query
            )
            data = response.json()
            meta = data.get("meta") or {}
            cost += float(meta.get("cost_usd") or 0.0)
            remaining = response.headers.get("X-RateLimit-Remaining", remaining)
            total = int(meta.get("count") or total)

            results = data.get("results") or []
            if not results:
                break
            papers.extend(_parse_work(w) for w in results)
            cursor = meta.get("next_cursor")
    except Exception as e:
        # I6: 페이지 중간에 실패해도 그때까지 이미 과금된 비용은 남는다. 호출자
        # (search.collect)가 예산 행에 반영할 수 있도록 예외에 실어 올린다 — 예외
        # 타입 자체(RateLimited.permanent 포함)는 그대로 보존해 재전파한다.
        e.cost_usd = cost
        raise

    logger.info(
        "[OpenAlex] query=%r %d-%d total=%d fetched=%d cost=$%.4f",
        query, year_from, year_to, total, len(papers), cost,
    )
    return OpenAlexResult(papers=papers, cost_usd=cost, remaining=remaining, total_count=total)
