import pytest
from app.models.analysis import Analysis
from app.models.paper import Paper, PaperExtraction
from app.prompts import MAP_INSTRUCTION, MAP_SCHEMA
from app.services import mapper


# 공용 ctx(분야·세부기술)에 이 파일이 필요로 하는 Analysis만 얹는다.
@pytest.fixture(name="ctx")
def mapper_ctx(ctx):
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="extracting", query_hash="h")
    db.add(a)
    db.commit()
    return db, a


def _paper(db, key, abstract):
    p = Paper(paper_key=key, title="T", abstract=abstract, year=2025, source="openalex")
    db.add(p)
    db.commit()
    return p


def test_pending_excludes_papers_without_abstract(ctx):
    db, a = ctx
    p1 = _paper(db, "k1", "있음")
    p2 = _paper(db, "k2", "")
    pending = mapper.pending_papers(db, [p1, p2])
    assert [p.paper_key for p in pending] == ["k1"]


def test_pending_excludes_cache_hits(ctx):
    db, a = ctx
    p1 = _paper(db, "k1", "있음")
    db.add(PaperExtraction(paper_key="k1", tech_summary="이미 있음", model_ver=mapper.model_ver()))
    db.commit()
    assert mapper.pending_papers(db, [p1]) == []


def test_model_ver_includes_extraction_schema_version(ctx):
    assert f"v{mapper.EXTRACTION_SCHEMA_VERSION}" in mapper.model_ver()


def test_pending_reextracts_when_schema_version_differs(ctx, monkeypatch):
    """스키마 버전이 바뀌면 옛 model_ver로 저장된 추출은 캐시 히트가 아니라
    재추출 대상이어야 한다 — 기존 행은 지우지 않고 그대로 둔 채(superseded)."""
    db, a = ctx
    p1 = _paper(db, "k1", "있음")
    old_ver = f"{mapper.settings.gemini_model}/{mapper.settings.thinking_map}/v1"
    db.add(PaperExtraction(paper_key="k1", tech_summary="옛 스키마 결과", model_ver=old_ver))
    db.commit()

    assert [p.paper_key for p in mapper.pending_papers(db, [p1])] == ["k1"]
    # 옛 행은 지워지지 않고 남아 있어야 한다.
    assert db.query(PaperExtraction).filter(PaperExtraction.model_ver == old_ver).count() == 1


def test_pending_reuses_extraction_from_another_subfield(ctx):
    """다른 세부기술이 이미 추출해 둔 논문은 재추출하지 않는다.

    추출 프롬프트에 세부기술이 없으므로(test_map_prompt_stays_subfield_independent가 고정) 같은
    논문의 추출 결과는 세부기술과 무관하게 같은 값이다. 세부기술마다 다시 돌리면
    같은 입력에 같은 일을 시키고 두 번 과금하는 것이다 — 실측 11%가 그랬다.
    """
    db, a = ctx
    p1 = _paper(db, "k1", "있음")
    db.add(PaperExtraction(paper_key="k1", tech_summary="다른 세부기술이 만든 결과",
                           model_ver=mapper.model_ver()))
    db.commit()
    assert mapper.pending_papers(db, [p1]) == []


def test_build_requests_carries_paper_key_as_the_request_key(ctx):
    db, a = ctx
    p1 = _paper(db, "k1", "초록 본문")
    reqs = mapper.build_requests([p1])
    assert reqs[0]["key"] == "k1"
    assert "초록 본문" in reqs[0]["request"]["contents"][0]["parts"][0]["text"]


def test_build_requests_serializes_sdk_types_into_expected_wire_shape(ctx):
    """SDK 소스(google/genai/batches.py::_InlinedRequest_to_mldev,
    google/genai/models.py::_GenerateContentConfig_to_mldev)로 확인한 실제 와이어
    구조: systemInstruction은 request 최상위, thinkingConfig/responseSchema 등은
    generationConfig 안에 camelCase로 중첩된다."""
    db, a = ctx
    p1 = _paper(db, "k1", "초록 본문")
    req = mapper.build_requests([p1])[0]["request"]

    assert req["systemInstruction"]["parts"][0]["text"] == MAP_INSTRUCTION
    assert req["generationConfig"]["responseMimeType"] == "application/json"
    assert req["generationConfig"]["responseSchema"] == MAP_SCHEMA
    assert req["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "LOW"
    # approach/improvement가 요청 스키마에 실려 있어야 새 필드를 모델에 요구할 수 있다.
    assert "approach" in MAP_SCHEMA["properties"]
    assert "improvement" in MAP_SCHEMA["properties"]
    assert "approach" in MAP_SCHEMA["required"]
    assert "improvement" in MAP_SCHEMA["required"]
    # 손으로 만든 snake_case 키가 남아있지 않은지 확인
    assert "system_instruction" not in req
    assert "generation_config" not in req


def test_estimate_tokens_korean_estimates_higher_than_ascii(ctx):
    db, a = ctx
    ascii_paper = _paper(db, "k1", "x" * 100)
    ascii_paper.title = "y" * 100
    db.commit()

    korean_paper = _paper(db, "k2", "가" * 100)
    korean_paper.title = "나" * 100
    db.commit()

    ascii_estimate = mapper.estimate_tokens([ascii_paper])
    korean_estimate = mapper.estimate_tokens([korean_paper])
    assert korean_estimate > ascii_estimate


def test_save_results_writes_extractions(ctx):
    db, a = ctx
    _paper(db, "k1", "있음")
    saved = mapper.save_results(db, [
        {"key": "k1", "tech_summary": "TSV 피치 개선", "achievement_type": "공정",
         "metrics": [{"name": "피치", "value": "20", "unit": "um"}],
         "approach": "저온 본딩 공정 적용", "improvement": "기존 대비 피치 절반 축소"},
    ])
    assert saved == 1
    row = db.query(PaperExtraction).one()
    assert row.tech_summary == "TSV 피치 개선"
    assert row.achievement_type == "공정"
    assert row.metrics_json[0]["unit"] == "um"
    assert row.approach == "저온 본딩 공정 적용"
    assert row.improvement == "기존 대비 피치 절반 축소"


def test_save_results_defaults_approach_and_improvement_to_empty_when_missing(ctx):
    """응답에 approach/improvement가 없어도(구 모델 응답 등) 저장이 실패하지 않고
    빈 문자열로 채워져야 한다."""
    db, a = ctx
    _paper(db, "k1", "있음")
    mapper.save_results(db, [
        {"key": "k1", "tech_summary": "A", "achievement_type": "공정", "metrics": []},
    ])
    row = db.query(PaperExtraction).one()
    assert row.approach == ""
    assert row.improvement == ""


def test_save_results_is_idempotent(ctx):
    db, a = ctx
    _paper(db, "k1", "있음")
    payload = [{"key": "k1", "tech_summary": "A", "achievement_type": "공정", "metrics": []}]
    mapper.save_results(db, payload)
    mapper.save_results(db, payload)
    assert db.query(PaperExtraction).count() == 1


def test_token_capped_chunk_stops_before_exceeding_token_budget(ctx, monkeypatch):
    """C2: 토큰 상한을 넘기기 직전에서 끊고, 넘는 나머지는 청크에서 빠져야 한다."""
    db, a = ctx
    p1 = _paper(db, "k1", "가" * 2000)
    p2 = _paper(db, "k2", "나" * 2000)
    reqs = mapper.build_requests([p1, p2])

    one_paper_tokens = mapper.estimate_tokens([p1])
    monkeypatch.setattr(mapper.settings, "batch_max_enqueued_tokens", one_paper_tokens + 10)

    capped = mapper.token_capped_chunk([p1, p2], reqs)
    assert [r["key"] for r in capped] == ["k1"]


def test_token_capped_chunk_always_keeps_at_least_one_request(ctx, monkeypatch):
    """논문 한 편만으로도 토큰 상한을 넘는 극단적인 경우에도 무한 대기에
    빠지지 않도록 최소 1건은 담아야 한다."""
    db, a = ctx
    p1 = _paper(db, "k1", "가" * 5000)
    reqs = mapper.build_requests([p1])
    monkeypatch.setattr(mapper.settings, "batch_max_enqueued_tokens", 1)

    capped = mapper.token_capped_chunk([p1], reqs)
    assert len(capped) == 1


def test_token_capped_chunk_keeps_everything_when_under_budget(ctx):
    db, a = ctx
    p1 = _paper(db, "k1", "짧음")
    p2 = _paper(db, "k2", "짧음")
    reqs = mapper.build_requests([p1, p2])
    capped = mapper.token_capped_chunk([p1, p2], reqs)
    assert len(capped) == 2


def test_estimate_llm_cost_usd_scales_with_paper_count_and_is_positive():
    cost_10 = mapper.estimate_llm_cost_usd(10)
    cost_100 = mapper.estimate_llm_cost_usd(100)
    assert cost_10 > 0
    assert cost_100 > cost_10


ACHIEVEMENT_TYPES = ["신소자", "신소재", "공정", "알고리즘", "아키텍처",
                     "성능향상", "시스템구현", "이론/해석", "기타"]


def test_map_schema_separates_metric_target_from_name():
    """측정 대상·조건이 지표명에 섞이면 같은 지표가 쪼개져 집계가 성립하지 않는다
    (실측: 재생에너지 2025에서 PCE가 7조각). target 필드로 분리한다."""
    item = MAP_SCHEMA["properties"]["metrics"]["items"]
    assert "target" in item["properties"]
    assert "target" in item["required"]


def test_map_schema_constrains_achievement_type_to_enum():
    """9종 지정인데 실제로는 17종이 저장돼 있었다(회로설계/회로 설계 등).
    이 값은 3단 reduce의 그룹 분할 키라 오염되면 그룹이 불필요하게 늘어난다."""
    assert MAP_SCHEMA["properties"]["achievement_type"]["enum"] == ACHIEVEMENT_TYPES


def test_map_instruction_states_metric_naming_rules():
    for phrase in ["물리량 이름만", "target", "ASCII"]:
        assert phrase in MAP_INSTRUCTION


def test_extraction_schema_version_is_pinned():
    """이 값을 올리면 기존 추출이 전량 무효화되어 재추출된다(22,059건 기준 약 $6).
    비용이 큰 되돌릴 수 없는 동작이므로 승인 없이 조용히 오르지 않게 못박는다.

    v3(2026-08-01): metrics에 target 필드 추가 + 지표명·단위 작성 규칙 도입.
    옛 지표명은 물질·조건이 섞여 있어(Single-junction PSC PCE) 집계가 성립하지
    않았다 — 정리하려면 전량 재추출이 필요해 사용자 승인을 받아 올렸다."""
    from app.services.mapper import EXTRACTION_SCHEMA_VERSION
    assert EXTRACTION_SCHEMA_VERSION == 3


def test_extraction_requests_pin_temperature_to_zero():
    """추출은 temperature 0으로 나가야 한다 — 지우면 API 기본값 1.0으로 되돌아간다.

    1.0에서는 같은 논문·같은 프롬프트의 achievement_type이 17% 어긋난다(전수
    19,904쌍에서 자기 일치 0.830). 그 값은 reducer.group_for_reduce의 3단 reduce
    그룹 분할 키이자 stats.by_achievement_type의 집계 축이라 보고서의 구조와 통계가
    함께 흔들린다. 0에서는 200/200 완전 재현이다(실측 2026-08-26).

    되돌리기 쉽고(한 줄) 되돌려도 아무것도 깨지지 않아 조용히 사라질 수 있는
    항목이라 여기서 못박는다. greedy 퇴화는 논문 400건으로 확인했다 — 3-gram
    반복률 최대값이 오히려 temperature 1.0보다 낮았다."""
    from app.models import Paper
    from app.services.mapper import build_requests

    papers = [Paper(paper_key="k1", title="T", abstract="A", year=2026)]
    cfg = build_requests(papers)[0]["request"]["generationConfig"]
    assert cfg["temperature"] == 0


def test_map_prompt_stays_subfield_independent():
    """추출 프롬프트에 세부기술이 섞이면 안 된다 — 캐시 공유의 전제다.

    uq_extraction이 (paper_key, model_ver)라 같은 논문의 추출 결과가 모든 세부기술에
    재사용된다(migration 0021). 그 재사용이 옳으려면 추출 입력이 세부기술과 무관해야
    한다. 여기서 세부기술명·검색식 같은 것을 프롬프트에 넣게 되면 캐시 키를 되돌려야
    하고, 안 되돌리면 A의 관점으로 뽑은 결과를 B가 자기 것인 양 쓴다.
    """
    import inspect

    from app import prompts

    # 입력을 만드는 함수는 title·abstract 외의 것을 받지 않는다.
    assert list(inspect.signature(prompts.map_user_text).parameters) == ["title", "abstract"]
    assert prompts.map_user_text("제목", "초록") == "제목: 제목\n\n초록: 초록"

    # 지시문도 세부기술을 언급하지 않는다.
    assert "세부기술" not in prompts.MAP_INSTRUCTION

    # build_requests가 만드는 요청 본문에도 논문 밖의 정보가 실리지 않는다.
    class _P:
        paper_key, title, abstract = "k", "제목", "초록"

    req = mapper.build_requests([_P()])[0]["request"]
    assert req["contents"][0]["parts"][0]["text"] == prompts.map_user_text("제목", "초록")
