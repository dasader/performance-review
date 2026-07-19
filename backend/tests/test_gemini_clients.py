import asyncio
import sys
import threading

from app.clients import gemini_batch
from app.config import Settings


def test_poll_async_runs_off_the_event_loop_thread(monkeypatch):
    """C4: poll()은 결과 파일 다운로드 + JSON 파싱을 동기로 하므로, async def 안에서
    그냥 부르면 event loop(=FastAPI 프로세스 전체)가 멈춘다. poll_async는 이를
    gemini_sync.py가 이미 쓰는 스레드풀로 보내야 한다."""
    seen = {}

    def fake_poll(job_name):
        seen["thread"] = threading.current_thread().name
        seen["main"] = threading.main_thread().name
        return "succeeded", []

    monkeypatch.setattr(gemini_batch, "poll", fake_poll)

    result = asyncio.run(gemini_batch.poll_async("job-1"))

    assert result == ("succeeded", [])
    assert seen["thread"] != seen["main"]
    assert seen["thread"].startswith("gemini-sync")


def test_submit_async_runs_off_the_event_loop_thread(monkeypatch):
    seen = {}

    def fake_submit(requests):
        seen["thread"] = threading.current_thread().name
        seen["main"] = threading.main_thread().name
        return "job-x"

    monkeypatch.setattr(gemini_batch, "submit", fake_submit)

    result = asyncio.run(gemini_batch.submit_async([{"key": "k1"}]))

    assert result == "job-x"
    assert seen["thread"] != seen["main"]
    assert seen["thread"].startswith("gemini-sync")


def test_import_succeeds_without_gemini_api_key(monkeypatch):
    """빈 GEMINI_API_KEY로도 import(=컨테이너 기동)가 실패하면 안 된다.
    클라이언트는 실제 호출 시점까지 지연 생성돼야 한다.

    google-genai SDK는 api_key=""가 falsy면 GEMINI_API_KEY/GOOGLE_API_KEY 환경변수로
    폴백하므로(conftest가 테스트 세션 전체에 test-key를 심어둔다), 그 폴백까지 막아야
    실제로 "빈 키" 상황을 재현한다.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.config.settings",
        Settings(gemini_api_key="", openalex_api_key="k", admin_key="a",
                  database_url="postgresql://x/y"),
    )
    for mod in ("app.clients.gemini_batch", "app.clients.gemini_sync"):
        sys.modules.pop(mod, None)

    import app.clients.gemini_batch  # noqa: F401
    import app.clients.gemini_sync  # noqa: F401
