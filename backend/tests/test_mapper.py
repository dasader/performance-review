import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.analysis import Analysis
from app.models.field import Field, Subfield
from app.models.paper import Paper, PaperExtraction
from app.prompts import MAP_INSTRUCTION, MAP_SCHEMA
from app.services import mapper


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    f = Field(name="양자", slug="quantum", order_no=1)
    db.add(f)
    db.flush()
    sf = Subfield(field_id=f.id, name="양자컴퓨팅", query="quantum computing")
    db.add(sf)
    db.flush()
    a = Analysis(subfield_id=sf.id, year=2025, status="extracting", query_hash="h")
    db.add(a)
    db.flush()
    db.commit()
    return db, a


def _paper(db, key, abstract):
    p = Paper(paper_key=key, title="T", abstract=abstract, year=2025, source="openalex",
              korea_flag=True)
    db.add(p)
    db.commit()
    return p


def test_pending_excludes_papers_without_abstract(ctx):
    db, a = ctx
    p1 = _paper(db, "k1", "있음")
    p2 = _paper(db, "k2", "")
    pending = mapper.pending_papers(db, a, [p1, p2])
    assert [p.paper_key for p in pending] == ["k1"]


def test_pending_excludes_cache_hits(ctx):
    db, a = ctx
    p1 = _paper(db, "k1", "있음")
    db.add(PaperExtraction(paper_key="k1", subfield_id=a.subfield_id,
                           tech_summary="이미 있음", model_ver=mapper.model_ver()))
    db.commit()
    assert mapper.pending_papers(db, a, [p1]) == []


def test_pending_ignores_extraction_from_another_subfield(ctx):
    db, a = ctx
    p1 = _paper(db, "k1", "있음")
    db.add(PaperExtraction(paper_key="k1", subfield_id=999,
                           tech_summary="다른 분야", model_ver=mapper.model_ver()))
    db.commit()
    assert [p.paper_key for p in mapper.pending_papers(db, a, [p1])] == ["k1"]


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
    saved = mapper.save_results(db, a, [
        {"key": "k1", "tech_summary": "TSV 피치 개선", "achievement_type": "공정",
         "metrics": [{"name": "피치", "value": "20", "unit": "um"}]},
    ])
    assert saved == 1
    row = db.query(PaperExtraction).one()
    assert row.tech_summary == "TSV 피치 개선"
    assert row.achievement_type == "공정"
    assert row.metrics_json[0]["unit"] == "um"


def test_save_results_is_idempotent(ctx):
    db, a = ctx
    _paper(db, "k1", "있음")
    payload = [{"key": "k1", "tech_summary": "A", "achievement_type": "공정", "metrics": []}]
    mapper.save_results(db, a, payload)
    mapper.save_results(db, a, payload)
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
