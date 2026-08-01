import asyncio
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class RateLimited(RuntimeError):
    """429. `permanent`가 True면 그날의 크레딧이 소진된 것이라 백오프해도 풀리지 않는다."""

    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


async def get_with_retry(
    url: str,
    *,
    client: httpx.AsyncClient,
    params: dict | None = None,
    service_name: str,
    context: str = "",
    max_attempts: int | None = None,
    timeout: float | None = None,
) -> httpx.Response:
    """GET + 지수 백오프. 429는 헤더로 일시/영구를 구분해 RateLimited로 올린다.
    max_attempts/timeout 기본값은 .env(HTTP_MAX_ATTEMPTS/HTTP_TIMEOUT_SECONDS)에서 온다."""
    max_attempts = max_attempts if max_attempts is not None else settings.http_max_attempts
    timeout = timeout if timeout is not None else settings.http_timeout_seconds
    for attempt in range(max_attempts):
        try:
            response = await client.get(url, params=params, timeout=timeout)
        except httpx.TimeoutException as e:
            raise RuntimeError(f"{service_name} 타임아웃: {context}") from e
        except httpx.RequestError as e:
            raise RuntimeError(f"{service_name} 네트워크 오류: {context}") from e

        if response.status_code == 429:
            # 잔여 요청 수와 잔여 예산 중 **하나라도** 바닥이면 소진이다. 요청 수만 보면
            # 놓친다 — 실측(2026-08-01)에서 X-RateLimit-Remaining=4인데
            # X-RateLimit-Remaining-USD=0.0004라 이 판정을 그대로 통과해 버렸다.
            for header in ("X-RateLimit-Remaining", "X-RateLimit-Remaining-USD"):
                value = response.headers.get(header)
                if value is not None and _as_float(value) <= 0:
                    raise RateLimited(f"{service_name} 일일 크레딧 소진", permanent=True)

            retry_after = _as_float(response.headers.get("Retry-After"))
            delay = retry_after if retry_after else 2 ** attempt
            # Retry-After가 상한을 넘으면 그만큼 자는 대신 permanent로 올린다.
            # runner.loop()는 활성 분석을 순차로 await하므로, 여기서 오래 자면 그 분석
            # 하나가 아니라 **잡 루프 전체**(나머지 분석 · batch 폴링 · resume_paused)가
            # 함께 멈춘다 — 실측으로 OpenAlex가 Retry-After 43,579초(약 12시간)를
            # 반환해 재추출 110건이 통째로 정지했다. permanent로 올리면 호출부가
            # analysis를 paused로 내리고 resume_paused가 자정에 재개한다(설계된 경로).
            if delay > settings.http_max_retry_after_seconds:
                raise RateLimited(
                    f"{service_name} 429 — 재시도까지 {delay:.0f}초 대기를 요구해 "
                    f"소진으로 처리합니다(상한 {settings.http_max_retry_after_seconds}초)",
                    permanent=True,
                )
            logger.warning("%s 429 (%d/%d), %.1fs 후 재시도", service_name, attempt + 1, max_attempts, delay)
            await asyncio.sleep(delay)
            continue

        if response.status_code >= 400:
            raise RuntimeError(f"{service_name} 오류 {response.status_code}: {context}")
        return response

    raise RateLimited(f"{service_name} 429 재시도 소진: {context}")


def _as_float(value: str | None) -> float:
    try:
        return float(value) if value is not None else 0.0
    except ValueError:
        return 0.0
