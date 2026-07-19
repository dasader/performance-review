import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.clients.openalex import OpenAlexResult
from app.database import Base
from app.models.field import Field, Subfield
from app.models.paper import Paper
from app.services import budget, search as search_module
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


async def test_collect_returns_openalex_total_count_alongside_merged_papers(db, monkeypatch):
    """C1: search.collect가 OpenAlex의 잘리기 전 total_count를 버리지 않고 그대로
    호출자에게 전달해야, 상한 가드가 잘린 papers 길이가 아니라 실제 전체 건수로
    판단할 수 있다."""
    sf = Subfield(field_id=1, name="HBM", query="hbm")

    async def fake_count_only(query, year_from, year_to, *, client):
        return 40000, 0.001

    async def fake_oa_search(query, year_from, year_to, *, client, limit):
        # 실제 openalex.search()처럼 limit(=max_papers_per_analysis)로 잘린 결과.
        return OpenAlexResult(papers=[], cost_usd=0.01, remaining="9", total_count=40000)

    async def fake_kci_search(query, year_from, year_to, *, client, limit):
        return []

    monkeypatch.setattr(search_module.openalex, "count_only", fake_count_only)
    monkeypatch.setattr(search_module.openalex, "search", fake_oa_search)
    monkeypatch.setattr(search_module.kci, "search", fake_kci_search)

    async with httpx.AsyncClient() as client:
        result = await search_module.collect(db, sf, 2024, 2024, client=client)

    assert result.total_count == 40000
    assert result.papers == []


async def test_collect_records_partial_openalex_cost_when_search_fails_midway(db, monkeypatch):
    """I6: openalex.search가 페이지 중간에 예외를 던져도, 그때까지 이미 과금된
    비용(예외에 실린 cost_usd)이 예산 행에 반영돼야 한다. 그러지 않으면 실패가
    반복될 때 spent_today가 실제 소비를 못 따라가 check_budget이 계속 승인해버린다."""
    sf = Subfield(field_id=1, name="HBM", query="hbm")

    async def fake_count_only(query, year_from, year_to, *, client):
        return 10, 0.001

    async def fake_oa_search_fails(query, year_from, year_to, *, client, limit):
        err = RuntimeError("OpenAlex 오류 500: 페이지 중간 실패")
        err.cost_usd = 0.02  # 이미 성공한 페이지 몇 건어치 비용
        raise err

    async def fake_kci_search(query, year_from, year_to, *, client, limit):
        return []

    monkeypatch.setattr(search_module.openalex, "count_only", fake_count_only)
    monkeypatch.setattr(search_module.openalex, "search", fake_oa_search_fails)
    monkeypatch.setattr(search_module.kci, "search", fake_kci_search)

    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError):
            await search_module.collect(db, sf, 2024, 2024, client=client)

    # count_only 비용(0.001) + search 중간까지의 비용(0.02)이 모두 반영돼야 한다.
    assert budget.spent_today(db) == pytest.approx(0.021)
