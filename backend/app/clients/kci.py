import logging
import xml.etree.ElementTree as ET

import httpx

from app.clients._doi import strip_doi_prefix
from app.clients._http import get_with_retry
from app.config import settings

logger = logging.getLogger(__name__)

API_URL = "https://open.kci.go.kr/po/openapi/openApiSearch.kci"


def _pick_by_lang(elements: list[ET.Element], preferred: str = "english") -> str:
    """KCI는 lang으로 "english" / "original"(보통 한국어) / "foreign"을 쓴다.
    영문 우선, 없으면 첫 비어있지 않은 값."""
    fallback = ""
    for el in elements:
        text = (el.text or "").strip()
        if not text:
            continue
        if el.get("lang") == preferred:
            return text
        if not fallback:
            fallback = text
    return fallback


def _int_or(text: str | None, default: int) -> int:
    try:
        return int((text or "").strip())
    except ValueError:
        return default


class KciApiError(RuntimeError):
    """KCI가 HTTP 200으로 돌려주는 본문 에러(키 만료·한도 초과 등).

    이걸 잡지 않으면 "결과 0건"과 구분되지 않아, 보고서가 국내지 성과를
    0건으로 단정하게 된다. 실제로는 API가 죽어 있는 상황이다.
    """


def _check_result_error(root: ET.Element) -> None:
    """`<outputData><result><resultMsg>`에 담겨 오는 에러를 검출한다.

    실측 응답 예(키 만료):
        <outputData><result><resultMsg>사용기간이 종료되었습니다.</resultMsg></result></outputData>
    정상 응답에는 `<record>`가 있고 이 `result` 블록이 없다.
    """
    msg_el = root.find("outputData/result/resultMsg")
    if msg_el is None:
        return
    message = (msg_el.text or "").strip()
    if not message:
        return
    raise KciApiError(f"KCI API 오류: {message}")


def _parse_search_xml(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise RuntimeError(f"KCI XML 파싱 실패: {e}") from e

    _check_result_error(root)

    papers: list[dict] = []
    for record in root.iter("record"):
        article = record.find("articleInfo")
        if article is None:
            continue
        article_id = article.get("article-id")
        if not article_id:
            continue

        journal_info = record.find("journalInfo")
        journal = year = None
        if journal_info is not None:
            journal_el = journal_info.find("journal-name")
            journal = (journal_el.text or "").strip() if journal_el is not None else None
            year_el = journal_info.find("pub-year")
            year = _int_or(year_el.text if year_el is not None else None, 0) or None

        doi_el = article.find("doi")
        doi = strip_doi_prefix(doi_el.text if doi_el is not None else None)
        citation_el = article.find("citation-count")
        citations = _int_or(citation_el.text if citation_el is not None else None, 0)

        papers.append({
            "paper_key": doi or f"kci:{article_id}",
            "title": _pick_by_lang(list(article.findall("title-group/article-title"))),
            "abstract": _pick_by_lang(list(article.findall("abstract-group/abstract"))),
            "year": year,
            "journal": journal,
            "doi": doi,
            "authors": [],
            "institutions": [],
            "countries": ["KR"],
            "citations": citations,
            "source": "kci",
            "korea_flag": True,
        })
    return papers


async def search(
    query: str, year_from: int, year_to: int, *, client: httpx.AsyncClient, limit: int
) -> list[dict]:
    """KCI 키워드 검색. 키 미설정 시 조용히 빈 리스트(graceful no-op).

    반대로 키가 **설정돼 있는데 API가 에러를 돌려주면** `KciApiError`를 올려 분석을
    실패시킨다. 이 서비스의 한국 논문 판정은 "KCI 전수 + OpenAlex KR"이므로, KCI가
    죽은 채 낸 결과는 국내지 성과를 0건으로 단정하게 되어 체계적으로 틀린다.
    KCI 없이 돌리려면 `KCI_API_KEY`를 비워 명시적으로 skip 모드를 택한다.

    KCI는 연도 필터 파라미터가 없어 응답을 받은 뒤 코드에서 연도를 거른다.
    """
    if not settings.kci_api_key:
        logger.info("[KCI] KCI_API_KEY 미설정 — 건너뜀")
        return []

    collected: list[dict] = []
    page = 1
    while len(collected) < limit:
        if page > settings.kci_max_pages:
            logger.warning(
                "[KCI] 페이지 상한(%d) 도달 — 결과가 잘렸을 수 있습니다: query=%r",
                settings.kci_max_pages, query,
            )
            break
        params = {
            "apiCode": "articleSearch",
            "key": settings.kci_api_key,
            "keyword": query,
            "displayCount": settings.kci_page_size,
            "page": page,
        }
        response = await get_with_retry(
            API_URL, client=client, params=params, service_name="KCI", context=query
        )
        batch = _parse_search_xml(response.text)
        if not batch:
            break
        collected.extend(p for p in batch if p["year"] and year_from <= p["year"] <= year_to)
        if len(batch) < settings.kci_page_size:
            break
        page += 1

    logger.info("[KCI] query=%r %d-%d fetched=%d", query, year_from, year_to, len(collected))
    return collected[:limit]
