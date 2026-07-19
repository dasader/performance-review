import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.field import Field, Subfield
from app.models.paper import Paper
from app.services.search import merge_papers, query_hash, upsert_papers


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _paper(key, **kw):
    base = {"paper_key": key, "title": "T", "abstract": "", "year": 2025, "journal": None,
            "doi": None, "authors": [], "institutions": [], "countries": [],
            "citations": 0, "source": "openalex", "korea_flag": True}
    base.update(kw)
    return base


def test_merge_prefers_the_record_with_an_abstract():
    oa = [_paper("10.1/x", abstract="", source="openalex")]
    kci = [_paper("10.1/x", abstract="있음", source="kci")]
    merged = merge_papers(oa, kci)
    assert len(merged) == 1
    assert merged[0]["abstract"] == "있음"


def test_merge_keeps_distinct_keys():
    merged = merge_papers([_paper("a")], [_paper("b")])
    assert {p["paper_key"] for p in merged} == {"a", "b"}


def test_query_hash_changes_when_query_changes():
    sf1 = Subfield(field_id=1, name="HBM", query="A", query_kci=None)
    sf2 = Subfield(field_id=1, name="HBM", query="B", query_kci=None)
    assert query_hash(sf1, 2024, 2026) != query_hash(sf2, 2024, 2026)
    assert query_hash(sf1, 2024, 2026) == query_hash(sf1, 2024, 2026)


def test_query_hash_changes_when_kci_override_changes():
    sf1 = Subfield(field_id=1, name="HBM", query="A", query_kci=None)
    sf2 = Subfield(field_id=1, name="HBM", query="A", query_kci="한글")
    assert query_hash(sf1, 2024, 2026) != query_hash(sf2, 2024, 2026)


def test_upsert_is_idempotent_and_fills_missing_abstract(db):
    upsert_papers(db, [_paper("k1", abstract="")])
    upsert_papers(db, [_paper("k1", abstract="채워짐")])
    rows = db.query(Paper).all()
    assert len(rows) == 1
    assert rows[0].abstract == "채워짐"
