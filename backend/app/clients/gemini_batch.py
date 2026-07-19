import json
import logging
import tempfile
from pathlib import Path

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=settings.gemini_api_key)

_TERMINAL_OK = "JOB_STATE_SUCCEEDED"
_TERMINAL_BAD = ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED")


def submit(requests: list[dict], *, thinking: str) -> str:
    """JSONL 파일을 업로드해 batch 잡을 만들고 잡 이름을 반환한다.

    inline이 아닌 파일 방식인 이유 — 논문 수천 건이면 inline 페이로드 상한을 넘는다.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
        for req in requests:
            fh.write(json.dumps(req, ensure_ascii=False) + "\n")
        path = Path(fh.name)

    try:
        uploaded = _client.files.upload(
            file=str(path), config={"mime_type": "application/jsonl"}
        )
        job = _client.batches.create(
            model=settings.gemini_model,
            src=uploaded.name,
            config=types.CreateBatchJobConfig(display_name=f"map-{len(requests)}"),
        )
    finally:
        path.unlink(missing_ok=True)

    logger.info("[batch] 제출 %d건 → %s (thinking=%s)", len(requests), job.name, thinking)
    return job.name


def poll(job_name: str) -> tuple[str, list[dict] | None]:
    """(state, results). state는 running | succeeded | failed."""
    job = _client.batches.get(name=job_name)
    state = job.state.name if hasattr(job.state, "name") else str(job.state)

    if state in _TERMINAL_BAD:
        logger.error("[batch] %s 실패: %s", job_name, state)
        return "failed", None
    if state != _TERMINAL_OK:
        return "running", None

    return "succeeded", _download_results(job)


def _download_results(job) -> list[dict]:
    """결과 JSONL을 파싱해 [{key, tech_summary, achievement_type, metrics}] 로 정규화한다.

    개별 요청이 실패했거나 JSON이 깨진 건은 조용히 건너뛴다 — 논문 한 편 때문에
    전체 분석을 죽이지 않는다.
    """
    raw = _client.files.download(file=job.dest.file_name)
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw

    results: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            key = record.get("key")
            candidates = record["response"]["candidates"]
            payload = json.loads(candidates[0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, ValueError, TypeError) as e:
            logger.warning("[batch] 결과 1건 파싱 실패: %s", e)
            continue
        if not key:
            continue
        results.append({
            "key": key,
            "tech_summary": payload.get("tech_summary", ""),
            "achievement_type": payload.get("achievement_type"),
            "metrics": payload.get("metrics") or [],
        })

    logger.info("[batch] 결과 수확 %d건", len(results))
    return results
