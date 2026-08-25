import asyncio
import json
import logging
import tempfile
from pathlib import Path

from google.genai import types

# 클라이언트 지연 생성과 스레드풀은 gemini_sync가 이미 갖고 있다 — 같은 것을 두 벌
# 두면 "키 없이도 컨테이너는 떠야 한다"는 불변식이 두 곳으로 갈라진다.
from app.clients import ollama
from app.clients.gemini_sync import _executor, _get_client
from app.config import settings

logger = logging.getLogger(__name__)


_TERMINAL_OK = "JOB_STATE_SUCCEEDED"
_TERMINAL_BAD = ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED")


def submit(requests: list[dict], analysis_id: int | None = None) -> str:
    """JSONL 파일을 업로드해 batch 잡을 만들고 잡 이름을 반환한다.

    inline이 아닌 파일 방식인 이유 — 논문 수천 건이면 inline 페이로드 상한을 넘는다.

    thinking 레벨은 build_requests()가 각 요청에 이미 박아 넣으므로 여기서는
    인자로 받지 않는다 — settings.thinking_map은 로그용으로만 참조한다.

    analysis_id는 순전히 로그 상관용이다(M17) — job.name만 남기면 나중에 로그에서
    이 batch가 어느 Analysis 것인지 역추적할 수 없다.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
        for req in requests:
            fh.write(json.dumps(req, ensure_ascii=False) + "\n")
        path = Path(fh.name)

    try:
        uploaded = _get_client().files.upload(
            file=str(path),
            config=types.UploadFileConfig(display_name=path.name, mime_type="jsonl"),
        )
        job = _get_client().batches.create(
            model=settings.gemini_model,
            src=uploaded.name,
            config=types.CreateBatchJobConfig(display_name=f"map-{len(requests)}"),
        )
    finally:
        path.unlink(missing_ok=True)

    logger.info(
        "[batch] 제출 %d건 → %s (analysis_id=%s, thinking=%s)",
        len(requests), job.name, analysis_id, settings.thinking_map,
    )
    return job.name


async def submit_async(requests: list[dict], analysis_id: int | None = None) -> str:
    """submit()의 async 래퍼(C4). submit()은 최대 1,000건 JSONL 업로드를 동기로
    하므로, async def 안에서 그냥 부르면 그동안 FastAPI 프로세스 전체(헬스체크,
    관리자 화면 등)가 멈춘다. gemini_sync.py가 이미 쓰는 스레드풀을 재사용한다."""
    if settings.llm_provider == "ollama":
        return await ollama.submit_async(requests, analysis_id)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, submit, requests, analysis_id)


async def poll_async(job_name: str) -> tuple[str, list[dict] | None]:
    """poll()의 async 래퍼(C4) — 이유는 submit_async와 동일. poll()은 결과 파일
    전체 다운로드 + JSON 파싱을 동기로 한다."""
    if settings.llm_provider == "ollama":
        return await ollama.poll_async(job_name)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, poll, job_name)


def poll(job_name: str) -> tuple[str, list[dict] | None]:
    """(state, results). state는 running | succeeded | failed."""
    job = _get_client().batches.get(name=job_name)
    state = job.state.name if hasattr(job.state, "name") else str(job.state)

    if state in _TERMINAL_BAD:
        logger.error("[batch] %s 실패: %s", job_name, state)
        return "failed", None
    if state != _TERMINAL_OK:
        return "running", None

    return "succeeded", _download_results(job)


_SAMPLE_LIMIT = 3
_SAMPLE_MAX_CHARS = 300


def _download_results(job) -> list[dict]:
    """결과 JSONL을 파싱해 [{key, tech_summary, achievement_type, metrics, approach,
    improvement}] 로 정규화한다.

    개별 요청이 실패했거나 JSON이 깨진 건은 건너뛴다 — 논문 한 편 때문에 전체
    분석을 죽이지는 않되, 총 건수 대비 스킵 건수를 집계해 호출자가 눈치채지
    못한 채 "성공"으로 넘어가지 않게 한다.

    M17: 스킵 건을 두 갈래로 구분해서 센다 — error_count(응답에 "response" 대신
    "error" 키가 있는 라인)와 parse_fail(구조가 예상과 달라 우리 파서가 못 읽은
    라인). 그리고 처음 몇 건의 문제 라인을 잘라서 로그에 남긴다 — 그러지 않으면
    응답 구조가 어긋난 첫 실행에서 "N건 중 N건 파싱 실패" 한 줄만 보고 원인을
    알 방법이 없다.
    """
    raw = _get_client().files.download(file=job.dest.file_name)
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw

    results: list[dict] = []
    total = 0
    skipped = 0
    error_count = 0
    samples: list[str] = []

    for line in text.splitlines():
        if not line.strip():
            continue
        total += 1
        try:
            record = json.loads(line)
            # ponytail: 개별 실패 요청이 "response" 대신 "error" 키를 갖는다는 것은
            # 리뷰어의 추측이고 실측된 구조가 아니다 — 실제 batch 출력을 확인하는 대로
            # 이 분기를 맞게 조정할 것. 지금은 방어적으로만 구분한다.
            if "error" in record and "response" not in record:
                error_count += 1
                raise ValueError(f"error 응답: {record.get('error')}")
            key = record.get("key")
            candidates = record["response"]["candidates"]
            payload = json.loads(candidates[0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, ValueError, TypeError) as e:
            skipped += 1
            if len(samples) < _SAMPLE_LIMIT:
                samples.append(line[:_SAMPLE_MAX_CHARS])
            logger.warning("[batch] 결과 1건 파싱 실패: %s", e)
            continue
        if not key:
            skipped += 1
            if len(samples) < _SAMPLE_LIMIT:
                samples.append(line[:_SAMPLE_MAX_CHARS])
            continue
        results.append({
            "key": key,
            "tech_summary": payload.get("tech_summary", ""),
            "achievement_type": payload.get("achievement_type"),
            "metrics": payload.get("metrics") or [],
            "approach": payload.get("approach", ""),
            "improvement": payload.get("improvement", ""),
        })

    if skipped:
        parse_fail = skipped - error_count
        summary = (
            "[batch] 결과 %d건 중 %d건 실패(error 응답 %d건, 파싱 실패 %d건)",
            total, skipped, error_count, parse_fail,
        )
        if total and skipped / total > 0.5:
            logger.critical(summary[0] + " — 과반 손실, 원인 점검 필요", *summary[1:])
        else:
            logger.error(*summary)
        if samples:
            logger.error(
                "[batch] 문제 라인 샘플(최대 %d건, %d자로 절단): %s",
                _SAMPLE_LIMIT, _SAMPLE_MAX_CHARS, samples,
            )

    logger.info("[batch] 결과 수확 %d건", len(results))
    return results
