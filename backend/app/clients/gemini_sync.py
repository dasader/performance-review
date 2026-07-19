import asyncio
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)

# 지연 생성: gemini_batch.py와 동일한 이유 — GEMINI_API_KEY가 비어 있으면
# genai.Client()가 즉시 ValueError를 던져 모듈 import(=컨테이너 기동)가 실패한다.
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


# 동기 전용 SDK를 async 코드에서 부르기 위한 명시적 스레드풀.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gemini-sync")


class _RequestBucket:
    """RPM 토큰버킷. Semaphore는 동시성 제한이지 rate 제한이 아니므로 시간 기반으로 센다."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._stamps: list[float] = []
        # 지연 생성: asyncio.Lock()은 최초 acquire 시점에 실행 중인 이벤트 루프에 바인딩되므로,
        # 여기서 즉시 만들면 이후 다른 루프(예: 테스트마다 새 루프를 만드는 pytest-asyncio)에서
        # RuntimeError가 난다.
        self._lock: asyncio.Lock | None = None

    async def acquire(self) -> None:
        if self.per_minute < 1:
            return
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            while True:
                now = time.monotonic()
                self._stamps = [t for t in self._stamps if now - t < 60]
                if len(self._stamps) < self.per_minute:
                    self._stamps.append(now)
                    return
                await asyncio.sleep(60 - (now - self._stamps[0]) + 0.1)


_bucket = _RequestBucket(settings.sync_rpm)


def _is_rate_limit(e: Exception) -> bool:
    return getattr(e, "status_code", None) == 429 or getattr(e, "code", None) == 429


async def generate(system: str, user: str, *, thinking: str, max_retries: int = 4) -> str:
    """단일 동기 생성 호출. RPM 버킷 통과 후 발사하고, 429는 지수 백오프."""
    config = types.GenerateContentConfig(
        system_instruction=system,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
    )

    def _call():
        return _get_client().models.generate_content(
            model=settings.gemini_model, contents=user, config=config
        )

    for attempt in range(max_retries + 1):
        await _bucket.acquire()
        try:
            response = await asyncio.get_running_loop().run_in_executor(_executor, _call)
            return response.text or ""
        except Exception as e:
            if _is_rate_limit(e) and attempt < max_retries:
                delay = 2 ** attempt + random.uniform(0, 1)
                logger.warning("Gemini 429 (%d/%d), %.1fs 후 재시도", attempt + 1, max_retries, delay)
                await asyncio.sleep(delay)
                continue
            raise
    # 루프는 매 반복마다 return 또는 raise로 끝나므로, 여기 도달하려면 range가 비어야 한다
    # (= max_retries < 0). 호출측 실수이며, 조용히 None을 반환하지 않도록 막는다.
    raise ValueError(f"max_retries는 0 이상이어야 합니다: {max_retries}")
