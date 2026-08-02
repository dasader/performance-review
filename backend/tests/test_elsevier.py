"""Elsevier ScienceDirect 초록 폴백 클라이언트.

이 클라이언트의 계약은 "절대 예외를 던지지 않는다"이다 — 보강 단계라 실패해도
분석이 멈추면 안 된다(KCI와 정반대. 설계 §5).
"""
import httpx
import pytest

from app.clients import elsevier
from app.config import settings


class _Resp:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Client:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _body(text):
    return {"full-text-retrieval-response": {"coredata": {"dc:description": text}}}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(settings, "elsevier_api_key", "test-key")


@pytest.mark.asyncio
async def test_returns_abstract_text():
    client = _Client([_Resp(200, _body("  회수된 초록 본문  "))])
    assert await elsevier.fetch_abstract("10.1016/j.x.2026.1", client=client) == "회수된 초록 본문"
    assert "10.1016/j.x.2026.1" in client.calls[0]


@pytest.mark.asyncio
async def test_returns_none_on_404_without_raising():
    """404는 정상적인 결과다 — ScienceDirect 미수록(실측 40건 중 2건)."""
    client = _Client([_Resp(404)])
    assert await elsevier.fetch_abstract("10.1016/j.x", client=client) is None


@pytest.mark.asyncio
async def test_returns_none_when_payload_has_no_abstract():
    """무자격 키는 HTTP 200에 메타데이터만 주고 dc:description이 빈다."""
    client = _Client([_Resp(200, {"full-text-retrieval-response": {"coredata": {"pii": "S1"}}})])
    assert await elsevier.fetch_abstract("10.1016/j.x", client=client) is None


@pytest.mark.asyncio
async def test_returns_none_on_network_error():
    client = _Client([httpx.ConnectError("boom")])
    assert await elsevier.fetch_abstract("10.1016/j.x", client=client) is None


@pytest.mark.asyncio
async def test_returns_none_on_malformed_json():
    client = _Client([_Resp(200, None)])
    assert await elsevier.fetch_abstract("10.1016/j.x", client=client) is None


@pytest.mark.asyncio
async def test_retries_once_on_429_then_succeeds(monkeypatch):
    slept = []

    async def fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr(elsevier.asyncio, "sleep", fake_sleep)
    client = _Client([_Resp(429, headers={"Retry-After": "3"}), _Resp(200, _body("본문"))])

    assert await elsevier.fetch_abstract("10.1016/j.x", client=client) == "본문"
    assert slept == [3.0]
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_gives_up_after_one_retry_on_429(monkeypatch):
    """무한 백오프를 두지 않는다 — 못 받아도 다음 실행에서 다시 시도된다."""
    async def fake_sleep(d):
        pass

    monkeypatch.setattr(elsevier.asyncio, "sleep", fake_sleep)
    client = _Client([_Resp(429), _Resp(429)])

    assert await elsevier.fetch_abstract("10.1016/j.x", client=client) is None
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_returns_none_without_calling_when_key_is_empty(monkeypatch):
    """키가 없으면 호출 자체를 하지 않는다 — 키 없이도 기존 동작이 그대로여야 한다."""
    monkeypatch.setattr(settings, "elsevier_api_key", "")
    client = _Client([])
    assert await elsevier.fetch_abstract("10.1016/j.x", client=client) is None
    assert client.calls == []
