import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.clients.openalex import OpenAlexResult
from app.database import Base
from app.models.field import Field, Subfield
from app.models.paper import Paper
from app.config import settings
from app.services import budget, search as search_module
from app.services import search
from app.services.search import merge_papers, query_hash, upsert_papers


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


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


def test_merge_marks_source_both_when_found_in_both_sources():
    """I10: merge_papers가 먼저 온 소스의 source 라벨만 유지하면, 양쪽에서 발견된
    논문이 전부 openalex로 집계되어 KCI 국내지 비율이 체계적으로 과소 표시된다.
    어느 쪽 순서로 병합하든 양쪽에서 발견된 논문은 "both"로 남아야 한다."""
    oa = [_paper("10.1/x", source="openalex")]
    kci = [_paper("10.1/x", source="kci")]
    merged = merge_papers(oa, kci)
    assert merged[0]["source"] == "both"

    merged_rev = merge_papers(kci, oa)
    assert merged_rev[0]["source"] == "both"


def test_merge_keeps_single_source_when_found_in_one_source():
    merged = merge_papers([_paper("a", source="openalex")], [])
    assert merged[0]["source"] == "openalex"
    merged_kci = merge_papers([], [_paper("b", source="kci")])
    assert merged_kci[0]["source"] == "kci"


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


def test_upsert_combines_source_across_reupserts(db):
    """I10: 병합은 단일 collect() 호출 안에서만 일어난다. 연도를 다시 검색해 이번에는
    KCI에서만 걸린 논문이라도, 과거에 openalex에서도 걸렸던 사실을 잃으면 안 된다."""
    upsert_papers(db, [_paper("k1", source="openalex")])
    upsert_papers(db, [_paper("k1", source="kci")])
    row = db.query(Paper).filter(Paper.paper_key == "k1").first()
    assert row.source == "both"


def test_upsert_preserves_both_source_when_reupserted_with_single_source(db):
    """한 번 both로 확인된 논문이, 이후 재검색에서 한쪽 소스만 다시 잡혀도(예: KCI가
    이번엔 안 걸림) both 라벨이 단일 소스로 되돌아가면 안 된다."""
    upsert_papers(db, [_paper("k1", source="both")])
    upsert_papers(db, [_paper("k1", source="openalex")])
    row = db.query(Paper).filter(Paper.paper_key == "k1").first()
    assert row.source == "both"


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


# ── Elsevier 초록 폴백 (search._fill_missing_abstracts) ──

def _sess():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def _cand(key, doi, abstract=""):
    return {"paper_key": key, "doi": doi, "abstract": abstract, "title": "T"}


async def test_fill_abstracts_skips_everything_when_key_is_empty(monkeypatch):
    """키가 없으면 클라이언트를 부르지 않는다 — 기존 동작이 그대로여야 한다."""
    monkeypatch.setattr(settings, "elsevier_api_key", "")
    called = []

    async def fake(doi, *, client):
        called.append(doi)
        return "x"

    monkeypatch.setattr(search.elsevier, "fetch_abstract", fake)
    papers = [_cand("k1", "10.1016/j.a")]

    assert await search._fill_missing_abstracts(_sess(), papers, client=None) == 0
    assert called == []


async def test_fill_abstracts_only_targets_elsevier_dois_without_abstract(monkeypatch):
    """ScienceDirect는 Elsevier 콘텐츠만 호스팅한다 — 다른 prefix는 확정 404라
    쿼터를 버릴 이유가 없다. 이미 초록이 있는 논문도 부르지 않는다."""
    monkeypatch.setattr(settings, "elsevier_api_key", "k")
    called = []

    async def fake(doi, *, client):
        called.append(doi)
        return "회수된 초록"

    monkeypatch.setattr(search.elsevier, "fetch_abstract", fake)
    papers = [
        _cand("k1", "10.1016/j.target"),                 # 대상
        _cand("k2", "10.1038/nature.x"),                 # Elsevier 아님
        _cand("k3", "10.1016/j.has", abstract="이미 있음"),  # 이미 초록 보유
        _cand("k4", None),                               # DOI 없음
    ]

    filled = await search._fill_missing_abstracts(_sess(), papers, client=None)

    assert called == ["10.1016/j.target"]
    assert filled == 1
    assert papers[0]["abstract"] == "회수된 초록"
    assert papers[2]["abstract"] == "이미 있음"


async def test_fill_abstracts_skips_papers_already_stored_with_abstract(monkeypatch):
    """DB에 이미 회수해 둔 논문을 매달 다시 받아오면 안 된다 —
    이 검사가 빠지면 KR 기준 연 36,000콜이 조용히 샌다(설계 §4-3)."""
    monkeypatch.setattr(settings, "elsevier_api_key", "k")
    db = _sess()
    db.add(Paper(paper_key="k1", title="T", abstract="예전에 회수한 초록",
                 doi="10.1016/j.a", source="openalex"))
    db.add(Paper(paper_key="k2", title="T", abstract="", doi="10.1016/j.b",
                 source="openalex"))
    db.commit()

    called = []

    async def fake(doi, *, client):
        called.append(doi)
        return "새 초록"

    monkeypatch.setattr(search.elsevier, "fetch_abstract", fake)
    papers = [_cand("k1", "10.1016/j.a"), _cand("k2", "10.1016/j.b")]

    filled = await search._fill_missing_abstracts(db, papers, client=None)

    assert called == ["10.1016/j.b"]
    assert filled == 1


async def test_fill_abstracts_absorbs_client_failures(monkeypatch):
    """개별 실패는 건너뛰고 나머지를 계속한다 — 분석이 멈추면 안 된다."""
    monkeypatch.setattr(settings, "elsevier_api_key", "k")

    async def fake(doi, *, client):
        if doi.endswith("bad"):
            raise RuntimeError("서비스 장애")
        return None if doi.endswith("none") else "본문"

    monkeypatch.setattr(search.elsevier, "fetch_abstract", fake)
    papers = [_cand("k1", "10.1016/j.bad"), _cand("k2", "10.1016/j.none"),
              _cand("k3", "10.1016/j.ok")]

    filled = await search._fill_missing_abstracts(_sess(), papers, client=None)

    assert filled == 1
    assert papers[2]["abstract"] == "본문"
    assert papers[0]["abstract"] == ""
