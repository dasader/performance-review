"""Elsevier ScienceDirect Article Retrieval — OpenAlex 결측 초록 회수.

OpenAlex는 초록을 Crossref에서 받아오는데 Elsevier가 Crossref에 예치하지 않아,
결측 초록의 66.8%가 Elsevier 게재분이다(실측). ScienceDirect Article Retrieval은
**무료 등록 키로도 초록을 준다** — 실측 88%(40건 중 35건), `openaccess=0`인 구독
전용 논문에서도 나온다. 전문(full text)은 구독이 필요하지만 우리에게 필요한 건 초록뿐이다.

(Scopus Abstract Retrieval은 같은 키로 401이다 — 기관 구독이 필요하다. 이쪽은 쓰지 않는다.)

**이 모듈의 계약: 어떤 경우에도 예외를 던지지 않는다.** 실패는 전부 None이다.
초록 회수는 *보강* 단계라 빠져도 결과가 틀리지 않고 그저 예전만큼만 분석될 뿐이며,
빠진 만큼은 stats의 no_abstract_count가 정확히 드러낸다. 검색 소스인 KCI가 실패 시
분석을 멈추는 것과 의도적으로 반대다(KCI는 빠지면 모집단이 조용히 줄어 결과가 틀린다).
"""

import asyncio
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.elsevier.com/content/article/doi/"

# ScienceDirect는 Elsevier 콘텐츠만 호스팅한다 — 다른 prefix는 확정적으로 404이므로
# 호출 자체를 하지 않는다(설계 §4). 호출부(search)가 이 값으로 대상을 거른다.
ELSEVIER_DOI_PREFIX = "10.1016/"

_TIMEOUT = 30.0
# 429를 만나면 이만큼 기다렸다가 한 번만 더 시도한다. 무한 백오프를 두지 않는 이유:
# 못 받아도 다음 실행에서 다시 시도되므로 이번 실행을 붙잡고 있을 이유가 없다.
_RETRY_AFTER_FALLBACK = 2.0


def _extract(payload: Any) -> str | None:
    """full-text-retrieval-response.coredata.dc:description을 꺼낸다.

    무자격 키는 HTTP 200에 메타데이터만 주고 이 필드가 빈다(응답 헤더 x-els-status가
    "WARNING - Unauthorized request results in minimized metadata response"). 그 경우도
    None으로 떨어져 호출부가 "회수 실패"로 처리한다.
    """
    if not isinstance(payload, dict):
        return None
    core = (payload.get("full-text-retrieval-response") or {}).get("coredata") or {}
    text = core.get("dc:description")
    return text.strip() if isinstance(text, str) and text.strip() else None


async def fetch_abstract(doi: str, *, client: httpx.AsyncClient) -> str | None:
    """DOI 하나의 초록을 회수한다. 실패는 전부 None이며 예외를 던지지 않는다.

    `_http.get_with_retry`를 쓰지 않는다 — 그 함수는 4xx에서 RuntimeError를 던지는데,
    여기서는 **404가 정상적인 결과**다(ScienceDirect 미수록, 실측 40건 중 2건).
    """
    if not settings.elsevier_api_key:
        return None

    headers = {
        "X-ELS-APIKey": settings.elsevier_api_key,
        "Accept": "application/json",
    }
    for attempt in range(2):  # 최초 + 429 재시도 1회
        try:
            response = await client.get(API_URL + doi, headers=headers, timeout=_TIMEOUT)
        except Exception as e:  # 네트워크 오류·타임아웃 전부 흡수한다.
            logger.debug("[초록회수] %s 요청 실패: %s", doi, e)
            return None

        if response.status_code == 429 and attempt == 0:
            delay = _RETRY_AFTER_FALLBACK
            try:
                delay = float(response.headers.get("Retry-After") or delay)
            except (TypeError, ValueError):
                pass
            await asyncio.sleep(delay)
            continue
        if response.status_code != 200:
            return None

        try:
            return _extract(response.json())
        except Exception:  # JSON이 아니거나 구조가 다른 경우.
            return None
    return None
