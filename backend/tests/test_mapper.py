import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.analysis import Analysis
from app.models.field import Field, Subfield
from app.models.paper import Paper, PaperExtraction
from app.services import mapper


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
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
