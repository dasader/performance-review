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


def test_merge_combines_abstract_and_authors_from_different_sources():
    """OpenAlex(abstract 없음, authors 있음) + KCI(abstract 있음, authors 없음)
    → 레코드 하나를 통째로 고르면 abstract나 authors 중 하나를 잃는다.
    필드 단위 병합이면 둘 다 살아남아야 한다."""
    oa = [_paper("10.1/x", abstract="", authors=["Kim"], source="openalex")]
    kci = [_paper("10.1/x", abstract="있음", authors=[], source="kci")]
    merged = merge_papers(oa, kci)
    assert len(merged) == 1
    assert merged[0]["abstract"] == "있음"
    assert merged[0]["authors"] == ["Kim"]


def test_merge_keeps_max_citations():
    oa = [_paper("10.1/y", citations=5, source="openalex")]
    kci = [_paper("10.1/y", citations=12, source="kci")]
    merged = merge_papers(oa, kci)
    assert merged[0]["citations"] == 12
    # 순서를 바꿔도 더 큰 값이 남아야 한다.
    merged_rev = merge_papers(kci, oa)
    assert merged_rev[0]["citations"] == 12


def test_merge_korea_flag_true_if_any_source_true():
    oa = [_paper("10.1/z", korea_flag=False, source="openalex")]
    kci = [_paper("10.1/z", korea_flag=True, source="kci")]
    merged = merge_papers(oa, kci)
    assert merged[0]["korea_flag"] is True


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
