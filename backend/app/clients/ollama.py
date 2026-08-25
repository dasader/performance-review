"""임시 LLM 교체용 Ollama Cloud 클라이언트 (2026-08, 약 1개월 후 Gemini로 원복).

`settings.llm_provider == "ollama"`일 때만 탄다 — `gemini_sync.generate`와
`gemini_batch.submit_async` / `poll_async`가 각자 첫 줄에서 이리로 넘긴다. 그래서
`runner.py` · `reducer.py` · `comparison.py` · `mapper.build_requests`는 한 줄도
바뀌지 않고, 원복은 `.env`의 `LLM_PROVIDER=gemini` 한 줄이면 된다.

Gemini와 다른 점 두 가지가 이 파일의 구조를 정한다.

① **구조화 출력이 없다.** `format`에 JSON Schema를 줘도, OpenAI 호환
   `/v1/chat/completions`의 `response_format: json_schema`를 줘도 **그냥 무시하고**
   마크다운 산문을 뱉는다(2026-08-25 실측, 둘 다). 유일하게 듣는 것은 native
   `/api/chat`의 `format: "json"`인데 이건 "JSON이기만 하면 되는" 모드라 스키마는
   강제하지 못한다. 그래서 스키마를 프롬프트(`_JSON_HINT`)로 알려주고 형태만
   `format: "json"`으로 보장받는다 — 실측으로 enum·필수 필드까지 지킨다.

② **batch API가 없다.** 대신 건당 2.4초로 끝나므로(Gemini batch는 최대 24시간)
   백그라운드 태스크로 청크를 돌리고 batch와 같은 submit/poll 인터페이스만 흉내낸다.

thinking 레벨은 Gemini 설정값을 그대로 넘긴다 — Ollama의 유효값이
`low|medium|high|max|true|false`라 `THINKING_MAP=low` · `THINKING_REDUCE=high`가
이름 그대로 통한다(잘못된 값은 400으로 즉시 거부된다).
"""

import asyncio
import json
import logging
from itertools import count

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# MAP_SCHEMA(prompts.py)를 프롬프트로 옮긴 것. Gemini는 response_schema로 강제하지만
# Ollama에는 그 수단이 없다(모듈 docstring ①). **prompts.MAP_SCHEMA를 고치면 여기도
# 함께 고쳐야 한다** — 어긋나면 스키마에 없는 필드가 조용히 저장된다.
# tests/test_ollama.py::test_json_hint_covers_every_map_schema_field 가 이를 고정한다.
_JSON_HINT = """

출력 형식(엄수): 아래 구조의 **JSON 객체 하나만** 출력하세요. 설명·머리말·마크다운
코드펜스 없이 순수 JSON만 출력합니다.
{"tech_summary": string, "achievement_type": "신소자"|"신소재"|"공정"|"알고리즘"|"아키텍처"|"성능향상"|"시스템구현"|"이론/해석"|"기타", "metrics": [{"name": string, "value": string, "unit": string, "target": string}], "approach": string, "improvement": string}"""

_RETRY_STATUSES = (408, 429, 500, 502, 503, 504)


def _timeout(seconds: float) -> httpx.Timeout:
    """HTTP_TIMEOUT_SECONDS(60초)를 그대로 쓰면 정상 응답이 죽는다 — 보고서 합성은
    실측 54.6초/11,608자가 걸린다. 추출과 보고서는 상한이 달라야 해서 호출부가 고른다."""
    return httpx.Timeout(seconds, connect=30.0)


def _url() -> str:
    return f"{settings.ollama_base_url.rstrip('/')}/api/chat"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.ollama_api_key}",
        "Content-Type": "application/json",
    }


async def _chat(
    client: httpx.AsyncClient,
    system: str,
    user: str,
    *,
    thinking: str,
    json_mode: bool,
    timeout: float,
    max_attempts: int = 4,
) -> str:
    """한 건 생성. 429·5xx는 지수 백오프로 재시도하고 그 외 4xx는 즉시 올린다
    (400 = 우리가 만든 요청이 틀린 것이라 재시도해도 같은 답이 온다)."""
    body: dict = {
        "model": settings.ollama_model,
        "stream": False,
        "think": thinking,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        body["format"] = "json"

    for attempt in range(max_attempts):
        try:
            response = await client.post(
                _url(), json=body, headers=_headers(), timeout=_timeout(timeout)
            )
            if response.status_code in _RETRY_STATUSES and attempt < max_attempts - 1:
                raise httpx.HTTPStatusError(
                    f"{response.status_code}", request=response.request, response=response
                )
            response.raise_for_status()
            return response.json()["message"]["content"] or ""
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status is not None and status not in _RETRY_STATUSES:
                raise
            if attempt == max_attempts - 1:
                raise
            delay = 2 ** attempt
            logger.warning(
                "[ollama] 호출 실패(%s), %ds 후 재시도 %d/%d", e, delay, attempt + 1, max_attempts
            )
            await asyncio.sleep(delay)
    # 루프는 매 반복이 return 또는 raise로 끝난다 — max_attempts < 1일 때만 여기 온다.
    raise ValueError(f"max_attempts는 1 이상이어야 합니다: {max_attempts}")


async def generate(system: str, user: str, *, thinking: str) -> str:
    """gemini_sync.generate와 같은 시그니처. 보고서 합성(reduce·rollup·로드맵 점검·
    국가 비교) 전용이라 json_mode를 쓰지 않는다 — 결과가 마크다운이어야 한다."""
    async with httpx.AsyncClient() as client:
        return await _chat(
            client, system, user, thinking=thinking, json_mode=False,
            timeout=settings.ollama_reduce_timeout_seconds,
        )


# ── batch 흉내 (모듈 docstring ②) ──────────────────────────────────────────
# job_id → 실행 중인 태스크. **메모리에만 산다** — 컨테이너가 재시작하면 비고,
# poll_async가 그 경우를 "결과 0건 성공"으로 돌려줘 runner가 재제출하게 만든다.
_jobs: dict[str, asyncio.Task] = {}
_seq = count(1)


def _texts(req: dict) -> tuple[str, str]:
    """mapper.build_requests가 만든 Gemini batch 요청에서 system/user 텍스트만 꺼낸다.

    build_requests를 provider별로 나누지 않으려는 것 — 청크 분할(`chunks`)과 토큰
    상한(`token_capped_chunk`)이 전부 그 함수의 결과 형태에 물려 있어서, 형식을
    갈라놓으면 그 둘도 같이 갈라야 한다.
    """
    body = req["request"]
    return (
        body["systemInstruction"]["parts"][0]["text"],
        body["contents"][0]["parts"][0]["text"],
    )


def parse_extraction(key: str, content: str) -> dict | None:
    """모델이 뱉은 JSON 문자열을 gemini_batch._download_results와 같은 형태로 정규화한다.
    깨진 응답은 None — 논문 한 편 때문에 청크 전체를 죽이지 않는다(같은 철학)."""
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        logger.warning("[ollama] %s: JSON 파싱 실패 — %s", key, (content or "")[:200])
        return None
    if not isinstance(payload, dict):
        logger.warning("[ollama] %s: JSON이 객체가 아님 — %s", key, str(payload)[:200])
        return None
    return {
        "key": key,
        "tech_summary": payload.get("tech_summary", ""),
        "achievement_type": payload.get("achievement_type"),
        "metrics": payload.get("metrics") or [],
        "approach": payload.get("approach", ""),
        "improvement": payload.get("improvement", ""),
    }


async def _extract_one(client: httpx.AsyncClient, req: dict) -> dict | None:
    key = req.get("key")
    if not key:
        # gemini_batch._download_results가 key 없는 결과를 버리는 것과 같은 이유 —
        # 논문에 되짚을 수 없는 추출은 저장할 곳이 없다.
        logger.warning("[ollama] key 없는 요청을 건너뛴다")
        return None
    try:
        system, user = _texts(req)
    except (KeyError, IndexError, TypeError) as e:
        logger.warning("[ollama] %s: 요청 구조를 읽지 못함 — %s", key, e)
        return None
    try:
        content = await _chat(
            client, system + _JSON_HINT, user, thinking=settings.thinking_map, json_mode=True,
            timeout=settings.ollama_extract_timeout_seconds,
            # 여기서 많이 재시도하면 안 된다 — 타임아웃까지 쓴 한 건이 백오프까지
            # 반복하며 청크 전체를 붙잡는다(180초 × 4회 = 12분). 진짜 재시도는
            # 잡 루프가 30초 뒤 다음 틱에 해 준다(아래 except 주석).
            max_attempts=2,
        )
    except Exception as e:
        # 실패해도 이 논문만 버린다. 남은 논문은 mapper.pending_papers가 다음 틱에
        # 다시 집어 오므로(추출 캐시에 안 들어갔으니) 재시도는 잡 루프가 해 준다.
        logger.warning("[ollama] %s: 호출 실패 — %s", key, e)
        return None
    return parse_extraction(key, content)


async def _run_chunk(requests: list[dict]) -> list[dict]:
    """청크 하나를 동시성 상한 안에서 처리한다. 실패 건은 걸러내고 나머지를 돌려준다 —
    남은 논문은 runner가 다음 틱에 pending으로 다시 집어 재시도한다."""
    sem = asyncio.Semaphore(settings.ollama_concurrency)

    async def one(req: dict) -> dict | None:
        async with sem:
            return await _extract_one(client, req)

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(one(r) for r in requests))

    kept = [r for r in results if r]
    if len(kept) < len(requests):
        logger.error("[ollama] 추출 %d건 중 %d건 실패", len(requests), len(requests) - len(kept))
    logger.info("[ollama] 결과 수확 %d건", len(kept))
    return kept


async def submit_async(requests: list[dict], analysis_id: int | None = None) -> str:
    """gemini_batch.submit_async와 같은 시그니처. 백그라운드 태스크를 띄우고 즉시
    핸들을 돌려줘, 잡 루프(30초 틱)가 다른 분석을 계속 돌릴 수 있게 한다."""
    job_id = f"ollama-{analysis_id}-{next(_seq)}"
    _jobs[job_id] = asyncio.create_task(_run_chunk(requests))
    logger.info(
        "[ollama] 제출 %d건 → %s (analysis_id=%s, model=%s, thinking=%s, 동시성=%d)",
        len(requests), job_id, analysis_id, settings.ollama_model,
        settings.thinking_map, settings.ollama_concurrency,
    )
    return job_id


async def poll_async(job_id: str) -> tuple[str, list[dict] | None]:
    """(state, results). gemini_batch.poll_async와 같은 계약."""
    task = _jobs.get(job_id)
    if task is None:
        # 컨테이너 재시작으로 _jobs가 비었다. "failed"로 올리면 분석이 통째로 죽으므로
        # 결과 0건 성공으로 돌려준다 — runner는 pending이 줄지 않은 것을 보고
        # extract_attempts를 올린 뒤 다음 틱에 같은 청크를 재제출한다(최대 3회).
        logger.warning("[ollama] %s: 진행 중이던 잡을 잃었다(재시작?) — 재제출에 맡긴다", job_id)
        return "succeeded", []
    if not task.done():
        return "running", None

    _jobs.pop(job_id, None)
    try:
        return "succeeded", task.result()
    except Exception:
        logger.exception("[ollama] %s: 청크 처리 실패", job_id)
        return "failed", None
