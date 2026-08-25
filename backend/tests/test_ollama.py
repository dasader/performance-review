"""임시 LLM 교체(Ollama Cloud, 2026-08)가 조용히 무너지지 않게 고정하는 테스트.

여기서 잡는 것은 전부 "돌려보면 성공처럼 보이는데 결과가 틀린" 종류다 —
캐시 네임스페이스가 안 갈려 재추출이 아예 안 일어난다든지, 프롬프트 스키마가
MAP_SCHEMA와 어긋나 필드가 조용히 비어 저장된다든지.
"""

import asyncio
import json

import pytest

from app.clients import ollama
from app.prompts import MAP_SCHEMA
from app.services import mapper


class _FakePaper:
    def __init__(self, key, title, abstract):
        self.paper_key = key
        self.title = title
        self.abstract = abstract


def test_texts_reads_what_build_requests_writes():
    """_texts는 mapper.build_requests의 출력 형식에 직접 의존한다 — Gemini 요청
    형식이 바뀌면 추출 전체가 조용히 0건이 되므로 실제 출력으로 확인한다."""
    papers = [_FakePaper("k1", "제목입니다", "초록입니다")]
    req = mapper.build_requests(papers)[0]

    system, user = ollama._texts(req)

    assert "과학기술 분석가" in system  # MAP_INSTRUCTION 본문
    assert "제목입니다" in user and "초록입니다" in user


def test_json_hint_covers_every_map_schema_field():
    """프롬프트 스키마 힌트가 MAP_SCHEMA와 어긋나면, Ollama는 스키마를 강제하지
    못하므로(response_schema 없음) 누락 필드가 조용히 빈 값으로 저장된다."""
    for field in MAP_SCHEMA["properties"]:
        assert field in ollama._JSON_HINT, f"{field}가 _JSON_HINT에 없다"
    for value in MAP_SCHEMA["properties"]["achievement_type"]["enum"]:
        assert value in ollama._JSON_HINT, f"achievement_type의 {value}가 _JSON_HINT에 없다"
    for field in MAP_SCHEMA["properties"]["metrics"]["items"]["properties"]:
        assert field in ollama._JSON_HINT, f"metrics.{field}가 _JSON_HINT에 없다"


def test_parse_extraction_normalizes_like_batch():
    payload = {
        "tech_summary": "요약",
        "achievement_type": "성능향상",
        "metrics": [{"name": "전력변환효율(PCE)", "value": "26.1", "unit": "%", "target": "PSC"}],
        "approach": "SAM 정공수송층",
        "improvement": "24.3%에서 향상",
    }
    result = ollama.parse_extraction("k1", json.dumps(payload, ensure_ascii=False))

    assert result == {"key": "k1", **payload}


@pytest.mark.parametrize("content", ["", "설명이 붙은 산문", '["배열은 안 된다"]', "{깨진 json"])
def test_parse_extraction_drops_broken_responses(content):
    """논문 한 편의 깨진 응답이 청크 전체를 죽이지 않는다(gemini_batch와 같은 철학)."""
    assert ollama.parse_extraction("k1", content) is None


def test_poll_of_lost_job_asks_for_resubmit_not_failure():
    """컨테이너 재시작으로 메모리 잡 테이블이 비면 'failed'가 아니라 결과 0건
    성공이어야 한다 — runner가 pending이 줄지 않은 것을 보고 재제출한다.
    failed로 올리면 분석이 통째로 죽어 관리자가 손으로 되살려야 한다."""
    state, results = asyncio.run(ollama.poll_async("ollama-없는잡-1"))

    assert (state, results) == ("succeeded", [])


def test_extract_cache_namespace_splits_by_provider(monkeypatch):
    """provider를 바꿨는데 model_ver이 그대로면 옛 Gemini 추출이 캐시 히트해
    새 모델로는 한 건도 재추출되지 않는다(= 교체가 조용히 무효화된다)."""
    monkeypatch.setattr(mapper.settings, "gemini_model", "gemini-3.1-flash-lite")
    monkeypatch.setattr(mapper.settings, "ollama_model", "deepseek-v4-flash:0731")

    monkeypatch.setattr(mapper.settings, "llm_provider", "gemini")
    gemini_ver = mapper.model_ver()
    monkeypatch.setattr(mapper.settings, "llm_provider", "ollama")
    ollama_ver = mapper.model_ver()

    assert gemini_ver != ollama_ver
    assert "gemini-3.1-flash-lite" in gemini_ver
    assert "deepseek-v4-flash:0731" in ollama_ver


def test_reduce_uses_ollama_when_provider_switched(monkeypatch):
    """reducer·comparison은 gemini_sync.generate만 부른다 — 그 안에서 넘기지 않으면
    보고서만 계속 Gemini로 나가 교체가 절반만 이뤄진다."""
    from app.clients import gemini_sync

    seen = {}

    async def fake_generate(system, user, *, thinking):
        seen.update(system=system, user=user, thinking=thinking)
        return "보고서"

    monkeypatch.setattr(gemini_sync.settings, "llm_provider", "ollama")
    monkeypatch.setattr(gemini_sync.ollama, "generate", fake_generate)

    result = asyncio.run(gemini_sync.generate("sys", "user", thinking="high"))

    assert result == "보고서"
    assert seen["thinking"] == "high"  # THINKING_REDUCE가 그대로 전달돼야 한다


def test_extract_routes_to_ollama_when_provider_switched(monkeypatch):
    """추출 경로(runner._do_extract → gemini_batch)도 같이 넘어가야 한다."""
    from app.clients import gemini_batch

    async def fake_submit(requests, analysis_id=None):
        return f"ollama-{analysis_id}-1"

    monkeypatch.setattr(gemini_batch.settings, "llm_provider", "ollama")
    monkeypatch.setattr(gemini_batch.ollama, "submit_async", fake_submit)

    assert asyncio.run(gemini_batch.submit_async([{"key": "k1"}], 7)) == "ollama-7-1"
