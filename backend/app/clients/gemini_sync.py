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


_RETRY_CODES = {429, 500, 502, 503, 504}


def _is_retryable(e: Exception) -> bool:
    """일시 장애인가 — 429(과다요청)와 5xx(서버측).

    **4xx를 여기 합치지 말 것.** 잘못된 스키마·만료된 키는 다섯 번 더 불러도 답이
    바뀌지 않고 과금만 는다. `app/clients/_http.py`가 OpenAlex에 대해 같은 선을 긋는다.

    5xx를 넣은 이유는 실측이다(2026-09-05, 로드맵 행 단위 판정 65행): `503 UNAVAILABLE
    — This model is currently experiencing high demand`가 산발적으로 떠서 그 행이
    판정 없이 남았다. 행 단위 구조는 콜 수가 65배라 이런 일시 오류를 그만큼 자주 만난다.
    """
    code = getattr(e, "status_code", None) or getattr(e, "code", None)
    if code in _RETRY_CODES:
        return True
    text = str(e)
    return any(k in text for k in ("UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL",
                                   "DEADLINE_EXCEEDED"))


async def generate(
    system: str,
    user: str,
    *,
    thinking: str,
    max_retries: int = 4,
    schema: dict | None = None,
) -> str:
    """단일 동기 생성 호출. RPM 버킷 통과 후 발사하고, 429는 지수 백오프.

    `schema`를 주면 JSON 모드로 나간다(`response_schema`는 API가 디코딩 단계에서
    강제하는 **계약**이다 — 프롬프트로 부탁하는 것과 다르다). 로드맵 점검의 행 단위
    판정이 이것을 쓴다.

    **산문 생성도 결정적 파라미터로 나간다** (`temperature=0, top_k=1, seed`, 2026-09-05).
    실측: 세부기술 보고서를 같은 입력으로 두 번 만들면 기본값에서 0/10 동일, temperature 0
    단독 6/10, seed 단독 무효, **top_k=1+seed에서 9/10** — 이 API의 temperature 0은 근접
    로짓에서 완전한 argmax가 아니라 top_k=1이 그것을 강제한다. 남는 1/10은 서빙 단.
    대가는 쟀다(`bench/prose_degeneration.py`): 길이·문장 수·인용 수·수치는 유지 또는
    증가, 어휘 다양도 −6%, 3-gram 반복률 +158% — 문장이 도는 퇴화가 아니라 소규모
    세부기술에서 같은 논문을 거듭 인용하는 편중. 표본을 읽고 채택했다.
    reduce가 재현되지 않으면 로드맵 판정이 논문 변화 없이 23% 흔들린다(전파 0.769).
    """
    config = types.GenerateContentConfig(
        system_instruction=system,
        thinking_config=types.ThinkingConfig(thinking_level=thinking),
        max_output_tokens=settings.gemini_max_output_tokens,
        temperature=0,
        top_k=1,
        seed=settings.gemini_seed,
        **({"response_mime_type": "application/json",
            "response_schema": schema} if schema else {}),
        # Flex 티어는 batch와 같은 반값이면서 batch의 최대 24시간 대기가 없다
        # (표준 $0.25/$1.50 → Flex $0.125/$0.75, gemini-3.1-flash-lite 기준).
        # 대가는 혼잡할 때 큐에 들어갈 수 있다는 것인데, 이 함수의 호출부(reduce·
        # rollup·로드맵 점검·국가 비교)는 전부 잡 루프가 30초 틱으로 돌리고 화면은
        # 폴링하는 구조라 몇 초~몇십 초 지연이 UX에 드러나지 않는다.
        # 큐잉이 실제로 길어지면 이 한 줄을 지워 표준 티어로 되돌린다.
        service_tier=types.ServiceTier.FLEX,
    )

    def _call():
        return _get_client().models.generate_content(
            model=settings.reduce_model, contents=user, config=config
        )

    for attempt in range(max_retries + 1):
        await _bucket.acquire()
        try:
            response = await asyncio.get_running_loop().run_in_executor(_executor, _call)
            return response.text or ""
        except Exception as e:
            if _is_retryable(e) and attempt < max_retries:
                delay = 2 ** attempt + random.uniform(0, 1)
                logger.warning("Gemini 일시 오류 (%d/%d), %.1fs 후 재시도: %s",
                               attempt + 1, max_retries, delay, str(e)[:120])
                await asyncio.sleep(delay)
                continue
            raise
    # 루프는 매 반복마다 return 또는 raise로 끝나므로, 여기 도달하려면 range가 비어야 한다
    # (= max_retries < 0). 호출측 실수이며, 조용히 None을 반환하지 않도록 막는다.
    raise ValueError(f"max_retries는 0 이상이어야 합니다: {max_retries}")
