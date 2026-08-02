"""_http.get_with_retry의 429 처리 — 잡 루프를 막지 않아야 한다."""
import httpx
import pytest

from app.clients import _http
from app.clients._http import RateLimited, get_with_retry


class _Resp:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


class _Client:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def get(self, url, params=None, timeout=None):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.mark.asyncio
async def test_long_retry_after_becomes_permanent_instead_of_sleeping(monkeypatch):
    """Retry-After가 상한을 넘으면 그만큼 자는 대신 permanent로 올린다.

    runner.loop()는 활성 분석을 순차로 await하므로, 여기서 12시간을 자면
    나머지 분석 전부와 batch 폴링·resume_paused까지 함께 멈춘다(실측: OpenAlex가
    Retry-After 43,579초를 반환해 잡 루프 전체가 정지).
    """
    slept = []

    async def fake_sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(_http.asyncio, "sleep", fake_sleep)
    client = _Client([_Resp(429, {"Retry-After": "43579", "X-RateLimit-Remaining": "4"})])

    with pytest.raises(RateLimited) as excinfo:
        await get_with_retry("http://x", client=client, service_name="OpenAlex")

    assert excinfo.value.permanent is True
    assert slept == []
    assert client.calls == 1


@pytest.mark.asyncio
async def test_exhausted_usd_budget_is_permanent_even_when_request_count_remains(monkeypatch):
    """요청 수가 남아 있어도 잔여 예산이 0이면 더 못 쓴다.

    실측: X-RateLimit-Remaining=4인데 X-RateLimit-Remaining-USD=0.0004라
    요청 수만 보는 판정으로는 소진을 못 잡았다.
    """
    async def fake_sleep(delay):
        raise AssertionError("잔여 예산이 없으면 자면 안 된다")

    monkeypatch.setattr(_http.asyncio, "sleep", fake_sleep)
    client = _Client([_Resp(429, {"X-RateLimit-Remaining": "4",
                                  "X-RateLimit-Remaining-USD": "0"})])

    with pytest.raises(RateLimited) as excinfo:
        await get_with_retry("http://x", client=client, service_name="OpenAlex")
    assert excinfo.value.permanent is True


@pytest.mark.asyncio
async def test_short_retry_after_still_backs_off_and_succeeds(monkeypatch):
    """짧은 Retry-After는 기존대로 대기 후 재시도한다."""
    slept = []

    async def fake_sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(_http.asyncio, "sleep", fake_sleep)
    client = _Client([
        _Resp(429, {"Retry-After": "2", "X-RateLimit-Remaining": "100",
                    "X-RateLimit-Remaining-USD": "0.5"}),
        _Resp(200),
    ])

    response = await get_with_retry("http://x", client=client, service_name="OpenAlex")
    assert response.status_code == 200
    assert slept == [2.0]


@pytest.mark.asyncio
async def test_transient_network_error_is_retried(monkeypatch):
    """연결 실패는 대개 일시적이라 재시도해야 한다.

    실측(2026-08-01 23:22 UTC): httpx.ConnectError 한 번에 분석 2건이
    search_attempts=0인 채로 곧장 failed가 됐다. 재시도 루프 안에 있으면서도
    네트워크 오류에서 즉시 탈출하고 있었다.
    """
    slept = []

    async def fake_sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(_http.asyncio, "sleep", fake_sleep)
    client = _Client([httpx.ConnectError("All connection attempts failed"), _Resp(200)])

    response = await get_with_retry("http://x", client=client, service_name="OpenAlex")
    assert response.status_code == 200
    assert client.calls == 2
    assert slept == [1]


@pytest.mark.asyncio
async def test_network_error_fails_only_after_exhausting_retries(monkeypatch):
    async def fake_sleep(delay):
        pass

    monkeypatch.setattr(_http.asyncio, "sleep", fake_sleep)
    client = _Client([httpx.ConnectError("boom")] * 3)

    with pytest.raises(RuntimeError, match="네트워크 오류"):
        await get_with_retry("http://x", client=client, service_name="OpenAlex",
                             max_attempts=3)
    assert client.calls == 3


@pytest.mark.asyncio
async def test_timeout_is_retried_and_reported_as_timeout(monkeypatch):
    """타임아웃도 재시도하되, 소진 시 메시지는 네트워크 오류와 구분해 남긴다."""
    async def fake_sleep(delay):
        pass

    monkeypatch.setattr(_http.asyncio, "sleep", fake_sleep)
    client = _Client([httpx.ReadTimeout("slow")] * 2)

    with pytest.raises(RuntimeError, match="타임아웃"):
        await get_with_retry("http://x", client=client, service_name="OpenAlex",
                             max_attempts=2)
    assert client.calls == 2
