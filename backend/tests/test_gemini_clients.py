import asyncio
import json
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

    def fake_submit(requests, analysis_id=None):
        seen["thread"] = threading.current_thread().name
        seen["main"] = threading.main_thread().name
        return "job-x"

    monkeypatch.setattr(gemini_batch, "submit", fake_submit)

    result = asyncio.run(gemini_batch.submit_async([{"key": "k1"}]))

    assert result == "job-x"
    assert seen["thread"] != seen["main"]
    assert seen["thread"].startswith("gemini-sync")


def test_submit_async_forwards_analysis_id(monkeypatch):
    """M17: job.name만 로그에 남으면 잡↔분석 역추적이 안 된다. submit_async가 받은
    analysis_id를 submit()까지 그대로 전달해야 로그에서 상관지을 수 있다."""
    seen = {}

    def fake_submit(requests, analysis_id=None):
        seen["analysis_id"] = analysis_id
        return "job-x"

    monkeypatch.setattr(gemini_batch, "submit", fake_submit)

    asyncio.run(gemini_batch.submit_async([{"key": "k1"}], analysis_id=42))

    assert seen["analysis_id"] == 42


def test_submit_logs_analysis_id(monkeypatch, caplog):
    class _FakeUploaded:
        name = "files/abc"

    class _FakeJob:
        name = "batches/xyz"

    class _FakeFiles:
        def upload(self, file, config):
            return _FakeUploaded()

    class _FakeBatches:
        def create(self, model, src, config):
            return _FakeJob()

    class _FakeClient:
        files = _FakeFiles()
        batches = _FakeBatches()

    monkeypatch.setattr(gemini_batch, "_get_client", lambda: _FakeClient())

    with caplog.at_level("INFO"):
        job_name = gemini_batch.submit([{"key": "k1"}], analysis_id=7)

    assert job_name == "batches/xyz"
    assert any("7" in r.message and "batches/xyz" in r.message for r in caplog.records)


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


def _good_line(key: str) -> str:
    payload = json.dumps({"tech_summary": "요약", "achievement_type": "공정", "metrics": []})
    return json.dumps({
        "key": key,
        "response": {"candidates": [{"content": {"parts": [{"text": payload}]}}]},
    })


class _FakeDest:
    file_name = "results/out.jsonl"


class _FakeJob:
    dest = _FakeDest()


def _fake_client_with_download(text: str):
    class _FakeFiles:
        def download(self, file):
            return text.encode("utf-8")

    class _FakeClient:
        files = _FakeFiles()

    return _FakeClient()


def test_download_results_distinguishes_error_from_parse_failure(monkeypatch, caplog):
    """M17: 실제 batch 출력에서 개별 실패 요청이 response 대신 error 키를 갖는다는 것은
    리뷰어의 추측(미실측)이다 — 방어적으로 error 키가 있는 라인과 우리 파서가 못 읽은
    (구조가 예상과 다른) 라인을 구분해서 세고 로그에 남겨야, "결과 전체 파싱 실패"라는
    뭉뚱그려진 한 줄 대신 원인을 구분해 볼 수 있다."""
    lines = "\n".join([
        _good_line("k1"),
        json.dumps({"key": "k2", "error": {"code": 500, "message": "internal"}}),
        json.dumps({"key": "k3", "response": {"candidates": []}}),  # IndexError로 파싱 실패
    ])
    monkeypatch.setattr(gemini_batch, "_get_client", lambda: _fake_client_with_download(lines))

    with caplog.at_level("WARNING"):
        results = gemini_batch._download_results(_FakeJob())

    assert len(results) == 1
    assert results[0]["key"] == "k1"
    messages = " ".join(r.message for r in caplog.records)
    assert "error" in messages  # error 키 라인이 별도로 언급됨
    assert "파싱 실패" in messages  # 구조 이상 라인도 언급됨


def test_download_results_logs_problem_line_samples(monkeypatch, caplog):
    """M17: 스킵 건수만 집계하면 첫 실전 실패에서 "결과 N건 중 N건 파싱 실패" 한 줄만
    보고 원인을 알 수 없다. 문제 라인 몇 건을 잘라서 로그 샘플로 남겨야 한다."""
    broken_lines = [json.dumps({"key": f"k{i}", "response": {"candidates": []}}) for i in range(5)]
    monkeypatch.setattr(
        gemini_batch, "_get_client", lambda: _fake_client_with_download("\n".join(broken_lines))
    )

    with caplog.at_level("ERROR"):
        results = gemini_batch._download_results(_FakeJob())

    assert results == []
    sample_logs = [r.message for r in caplog.records if "샘플" in r.message]
    assert len(sample_logs) == 1
    # 최대 3건만 샘플로 남겨야 한다(5건 전부 남기지 않음).
    assert sample_logs[0].count('"key"') <= 3


def test_download_results_passes_through_approach_and_improvement(monkeypatch):
    payload = json.dumps({
        "tech_summary": "요약", "achievement_type": "공정", "metrics": [],
        "approach": "저온 본딩 공정 적용", "improvement": "기존 대비 피치 절반 축소",
    })
    line = json.dumps({
        "key": "k1",
        "response": {"candidates": [{"content": {"parts": [{"text": payload}]}}]},
    })
    monkeypatch.setattr(gemini_batch, "_get_client", lambda: _fake_client_with_download(line))

    results = gemini_batch._download_results(_FakeJob())

    assert results[0]["approach"] == "저온 본딩 공정 적용"
    assert results[0]["improvement"] == "기존 대비 피치 절반 축소"


def test_download_results_defaults_approach_and_improvement_when_absent(monkeypatch):
    """모델 응답에 approach/improvement가 없어도(구 응답 등) 빈 문자열로 채워야
    save_results로 넘어가는 데이터가 KeyError 없이 안전해야 한다."""
    monkeypatch.setattr(gemini_batch, "_get_client", lambda: _fake_client_with_download(_good_line("k1")))

    results = gemini_batch._download_results(_FakeJob())

    assert results[0]["approach"] == ""
    assert results[0]["improvement"] == ""
