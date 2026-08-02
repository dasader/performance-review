import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.field import Field, Subfield
from app.models.analysis import Analysis, AnalysisPaper
from app.models.paper import Paper, PaperExtraction


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def test_subfield_holds_queries_and_analysis_is_unique_per_year():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()

    f = Field(name="반도체·디스플레이", slug="semiconductor", order_no=1)
    db.add(f)
    db.flush()
    sf = Subfield(field_id=f.id, name="HBM", query="HBM memory", query_kci=None)
    db.add(sf)
    db.flush()

    db.add(Analysis(subfield_id=sf.id, year=2025, status="pending", query_hash="abc"))
    db.commit()

    assert db.query(Subfield).one().query == "HBM memory"
    assert db.query(Analysis).one().status == "pending"


def test_uq_subfield_name_rejects_duplicate():
    db = _session()
    f = Field(name="반도체·디스플레이", slug="semiconductor", order_no=1)
    db.add(f)
    db.flush()

    db.add(Subfield(field_id=f.id, name="HBM", query="HBM memory"))
    db.commit()

    db.add(Subfield(field_id=f.id, name="HBM", query="다른 쿼리"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_uq_extraction_rejects_duplicate():
    db = _session()
    f = Field(name="반도체·디스플레이", slug="semiconductor", order_no=1)
    db.add(f)
    db.flush()
    sf = Subfield(field_id=f.id, name="HBM", query="HBM memory")
    db.add(sf)
    db.flush()

    db.add(PaperExtraction(paper_key="paper-1", subfield_id=sf.id, model_ver="v1"))
    db.commit()

    db.add(PaperExtraction(paper_key="paper-1", subfield_id=sf.id, model_ver="v1"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_uq_analysis_year_rejects_duplicate():
    db = _session()
    f = Field(name="반도체·디스플레이", slug="semiconductor", order_no=1)
    db.add(f)
    db.flush()
    sf = Subfield(field_id=f.id, name="HBM", query="HBM memory")
    db.add(sf)
    db.flush()

    db.add(Analysis(subfield_id=sf.id, year=2025, status="pending", query_hash="abc"))
    db.commit()

    db.add(Analysis(subfield_id=sf.id, year=2025, status="pending", query_hash="def"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_uq_analysis_paper_rejects_duplicate():
    db = _session()
    f = Field(name="반도체·디스플레이", slug="semiconductor", order_no=1)
    db.add(f)
    db.flush()
    sf = Subfield(field_id=f.id, name="HBM", query="HBM memory")
    db.add(sf)
    db.flush()
    analysis = Analysis(subfield_id=sf.id, year=2025, status="pending", query_hash="abc")
    paper = Paper(paper_key="paper-1", source="openalex")
    db.add_all([analysis, paper])
    db.flush()

    db.add(AnalysisPaper(analysis_id=analysis.id, paper_id=paper.id))
    db.commit()

    db.add(AnalysisPaper(analysis_id=analysis.id, paper_id=paper.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_analysis_sections_json_defaults_to_empty_list():
    """단일 reduce는 그룹이 하나뿐이라 세부 보고서가 없다 — 기본값이 빈 리스트여야
    화면이 '없음'을 판정할 수 있다."""
    db = _session()
    a = Analysis(subfield_id=1, year=2025, status="pending", query_hash="h")
    db.add(a)
    db.commit()
    db.refresh(a)
    assert a.sections_json == []


def test_analysis_sections_json_roundtrips_group_order():
    """그룹 순서가 보고서 구성 순서다 — 저장·조회에서 순서가 보존돼야 한다."""
    db = _session()
    sections = [{"name": "알고리즘", "body": "## 개괄\n본문"},
                {"name": "신소재", "body": "## 개괄\n다른 본문"}]
    a = Analysis(subfield_id=1, year=2026, status="done", query_hash="h",
                 sections_json=sections)
    db.add(a)
    db.commit()
    db.refresh(a)
    assert [x["name"] for x in a.sections_json] == ["알고리즘", "신소재"]
