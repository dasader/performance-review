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


# ── 보고서 합성 전용 모델 (gemini_model_reduce) ──

def _settings(**kw):
    base = dict(gemini_api_key="k", openalex_api_key="k", admin_key="k",
                database_url="postgresql://t/t")
    return Settings(**{**base, **kw})


def test_reduce_model_defaults_to_the_extraction_model():
    """설정을 추가해도 기존 동작이 그대로여야 한다 — 비워 두면 추출과 같은 모델."""
    s = _settings(gemini_model="gemini-3.1-flash-lite")
    assert s.gemini_model_reduce == ""
    assert s.reduce_model == "gemini-3.1-flash-lite"


def test_reduce_model_overrides_only_report_synthesis():
    s = _settings(gemini_model="gemini-3.1-flash-lite", gemini_model_reduce="gemini-3.6-flash")
    assert s.reduce_model == "gemini-3.6-flash"
    assert s.gemini_model == "gemini-3.1-flash-lite"   # 추출 모델은 그대로


def test_reduce_model_is_not_part_of_the_extraction_cache_key(monkeypatch):
    """이 분리의 존재 이유. reduce 모델을 바꿨다고 추출 캐시가 무효화되면
    "보고서만 다른 모델로" 판단에 매번 전량 재추출 비용(22,000여 건, 약 $6)이 붙는다."""
    from app.config import settings as live
    from app.services import mapper

    before = mapper.model_ver()
    monkeypatch.setattr(live, "gemini_model_reduce", "gemini-3.6-flash")
    assert mapper.model_ver() == before


def test_generate_uses_the_reduce_model(monkeypatch):
    """gemini_sync.generate는 추출용 모델이 아니라 reduce 모델로 쏴야 한다."""
    from app.clients import gemini_sync

    seen = {}

    class _FakeModels:
        def generate_content(self, *, model, contents, config):
            seen["model"] = model
            return type("R", (), {"text": "ok"})()

    monkeypatch.setattr(gemini_sync, "_get_client",
                        lambda: type("C", (), {"models": _FakeModels()})())
    # app.config.settings가 아니라 이 모듈이 실제로 들고 있는 객체를 고친다 —
    # 위 test_import_succeeds_without_gemini_api_key가 gemini_sync를 sys.modules에서
    # 지우고 재import하므로, 그 뒤로 둘은 서로 다른 Settings 인스턴스다.
    monkeypatch.setattr(gemini_sync.settings, "gemini_model", "gemini-3.1-flash-lite")
    monkeypatch.setattr(gemini_sync.settings, "gemini_model_reduce", "gemini-3.6-flash")

    assert asyncio.run(gemini_sync.generate("sys", "user", thinking="high")) == "ok"
    assert seen["model"] == "gemini-3.6-flash"


def test_reduce_calls_use_the_flex_service_tier(monkeypatch):
    """보고서 합성은 Flex 티어로 나가야 한다 — 표준가의 절반이다.

    reduce·rollup·로드맵 점검·국가 비교는 전부 잡 루프가 30초 틱으로 돌리고 화면은
    폴링하므로, Flex의 대가(혼잡 시 큐잉)가 UX에 드러나지 않는다. 실측(2026-08-25,
    gemini-3.1-flash-lite): 표준 $0.25/$1.50 → Flex $0.125/$0.75.
    이 한 줄이 빠지면 조용히 두 배를 낸다.
    """
    from google.genai import types

    from app.clients import gemini_sync

    captured = {}

    class _FakeModels:
        def generate_content(self, *, model, contents, config):
            captured["config"] = config

            class _R:
                text = "본문"

            return _R()

    class _FakeClient:
        models = _FakeModels()

    monkeypatch.setattr(gemini_sync, "_get_client", lambda: _FakeClient())

    asyncio.run(gemini_sync.generate("지시", "입력", thinking="high"))

    assert captured["config"].service_tier == types.ServiceTier.FLEX


def test_sync_generate_retries_transient_server_errors_but_not_4xx():
    """429뿐 아니라 5xx도 재시도한다 — 4xx는 아니다.

    행 단위 로드맵 판정은 콜 수가 65배라 일시 503을 그만큼 자주 만난다. 실측
    (2026-09-05)에서 재시도가 없어 그 행이 판정 없이 남았다. 반대로 4xx를 재시도하면
    답이 바뀌지 않는 요청을 다섯 번 과금할 뿐이다.
    """
    from app.clients.gemini_sync import _is_retryable

    class E(Exception):
        def __init__(self, code):
            self.code = code

    assert _is_retryable(E(429))
    for code in (500, 502, 503, 504):
        assert _is_retryable(E(code)), code
    for code in (400, 401, 403, 404, 422):
        assert not _is_retryable(E(code)), code
    # SDK가 코드를 노출하지 않고 문자열만 줄 때도 잡는다.
    assert _is_retryable(Exception("503 UNAVAILABLE. high demand"))
    assert not _is_retryable(Exception("400 INVALID_ARGUMENT"))


def test_sync_client_singleton_survives_concurrent_first_calls(monkeypatch):
    """첫 호출이 동시에 6개 들어와도 Client는 하나만 만들어진다.

    잠금이 없던 시절 컨테이너 재시작 직후 첫 틱에서 판정 3행이 "client has been closed"로
    죽었다 — 스레드마다 만든 Client 중 밀려난 것이 GC되며 연결을 닫은 것."""
    import threading
    from app.clients import gemini_sync

    made = []

    class FakeClient:
        def __init__(self, api_key):
            made.append(api_key)

    monkeypatch.setattr(gemini_sync.genai, "Client", FakeClient)
    monkeypatch.setattr(gemini_sync, "_client", None)
    got = []
    ts = [threading.Thread(target=lambda: got.append(gemini_sync._get_client())) for _ in range(6)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert len(made) == 1, f"Client가 {len(made)}번 만들어졌다"
    assert all(g is got[0] for g in got)
