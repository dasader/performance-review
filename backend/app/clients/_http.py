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
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining is not None and _as_float(remaining) <= 0:
                raise RateLimited(f"{service_name} 일일 크레딧 소진", permanent=True)
            retry_after = _as_float(response.headers.get("Retry-After"))
            delay = retry_after if retry_after else 2 ** attempt
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
