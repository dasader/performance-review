import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.clients._http import RateLimited
from app.config import settings
from app.database import Base
from app.models.analysis import Analysis, AnalysisPaper
from app.models.field import Field, Subfield
from app.models.paper import Paper
from app.services import budget, runner, search


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
    db.commit()
    return db, sf


def _search_paper(key, **kw):
    base = {"paper_key": key, "title": "T", "abstract": "A", "year": 2025, "journal": None,
            "doi": None, "authors": [], "institutions": [], "countries": [],
            "citations": 0, "source": "openalex", "korea_flag": True}
    base.update(kw)
    return base


def test_enqueue_creates_one_analysis_per_year(ctx):
    db, sf = ctx
    created = runner.enqueue(db, sf, 2024, 2026, force=False)
    assert {a.year for a in created} == {2024, 2025, 2026}
    assert all(a.status == "pending" for a in created)


def test_enqueue_skips_done_analysis_with_matching_hash(ctx):
    db, sf = ctx
    first = runner.enqueue(db, sf, 2025, 2025, force=False)[0]
    first.status = "done"
    db.commit()

    again = runner.enqueue(db, sf, 2025, 2025, force=False)
    assert again == []
    assert db.query(Analysis).count() == 1


def test_enqueue_reruns_when_query_changed(ctx):
    db, sf = ctx
    first = runner.enqueue(db, sf, 2025, 2025, force=False)[0]
    first.status = "done"
    db.commit()

    sf.query = "quantum error correction"   # 검색식 변경 → query_hash 불일치
    db.commit()

    again = runner.enqueue(db, sf, 2025, 2025, force=False)
    assert len(again) == 1
    assert again[0].status == "pending"
    assert db.query(Analysis).count() == 1  # 같은 행을 재사용


def test_enqueue_force_reruns_even_when_fresh(ctx):
    db, sf = ctx
    first = runner.enqueue(db, sf, 2025, 2025, force=False)[0]
    first.status = "done"
    db.commit()

    again = runner.enqueue(db, sf, 2025, 2025, force=True)
    assert len(again) == 1


def test_step_labels_are_korean():
    assert runner.STEP_LABELS["searching"] == "논문 검색 중"
    assert runner.STEP_LABELS["extracting"] == "성과 추출 중"
    assert runner.STEP_LABELS["reducing"] == "보고서 작성 중"
    assert runner.STEP_LABELS["done"] == "완료"


def test_resume_paused_returns_pending_when_budget_has_room(ctx):
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="paused", query_hash="h",
                 error="OpenAlex 일일 크레딧 소진 — 내일 자동 재개됩니다.")
    db.add(a)
    db.commit()

    runner.resume_paused(db)
    db.refresh(a)
    assert a.status == "pending"
    assert a.error is None


def test_resume_paused_stays_paused_when_budget_exhausted(ctx):
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="paused", query_hash="h", error="msg")
    db.add(a)
    db.commit()
    budget.record_usage(db, settings.openalex_daily_budget_usd, None)

    runner.resume_paused(db)
    db.refresh(a)
    assert a.status == "paused"
    assert a.error == "msg"


async def test_extract_attempts_resets_when_progress_is_made(ctx, monkeypatch):
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="extracting", query_hash="h",
                 batch_job_id="job-1", extract_attempts=2)
    db.add(a)
    db.commit()

    p = Paper(paper_key="k1", title="T", abstract="A", year=2025, source="openalex",
              korea_flag=True)
    db.add(p)
    db.commit()
    db.add(AnalysisPaper(analysis_id=a.id, paper_id=p.id))
    db.commit()

    # 첫 pending_papers 호출(before)은 1건, 두번째(still_pending, 저장 후)는 0건 —
    # 정상적으로 추출이 진행된 상황을 흉내낸다.
    calls = {"n": 0}

    def fake_pending(db, analysis, papers):
        calls["n"] += 1
        return [] if calls["n"] > 1 else [p]

    monkeypatch.setattr(runner.mapper, "pending_papers", fake_pending)
    monkeypatch.setattr(runner.mapper, "save_results", lambda db, a, results: 1)
    monkeypatch.setattr(runner.gemini_batch, "poll", lambda job_id: ("succeeded", []))

    await runner.advance(db, a)
    db.refresh(a)
    assert a.extract_attempts == 0
    assert a.status == "reducing"


async def test_extract_attempts_fails_after_max_when_no_progress(ctx, monkeypatch):
    """파싱 불가 논문 때문에 pending 건수가 줄지 않는 상황이 max_extract_attempts회
    반복되면 무한 재제출 대신 failed로 전환돼야 한다."""
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="extracting", query_hash="h")
    db.add(a)
    db.commit()

    p = Paper(paper_key="k1", title="T", abstract="A", year=2025, source="openalex",
              korea_flag=True)
    db.add(p)
    db.commit()
    db.add(AnalysisPaper(analysis_id=a.id, paper_id=p.id))
    db.commit()

    # pending_papers는 항상 같은 논문을 반환 — 저장해도 줄지 않는 상황(파싱 실패) 흉내.
    monkeypatch.setattr(runner.mapper, "pending_papers", lambda db, a, papers: [p])
    monkeypatch.setattr(runner.mapper, "save_results", lambda db, a, results: 0)
    monkeypatch.setattr(runner.gemini_batch, "poll", lambda job_id: ("succeeded", []))

    for i in range(settings.max_extract_attempts):
        a.batch_job_id = f"job-{i}"
        db.commit()
        await runner.advance(db, a)
        db.refresh(a)

    assert a.status == "failed"
    assert a.error is not None
    assert "추출" in a.error


async def test_do_search_blocks_on_total_count_not_truncated_papers_len(ctx, monkeypatch):
    """C1: search.collect가 반환하는 papers는 openalex.search(limit=max_papers_per_analysis)
    호출 결과라 구조적으로 상한을 넘을 수 없다. len(papers) 기준 가드는 절대 발동하지
    않으므로, 잘리기 전 전체 건수(total_count)로 판단해야 실제로 차단된다."""
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="searching", query_hash="h")
    db.add(a)
    db.commit()

    async def fake_collect(db, subfield, year_from, year_to, *, client):
        # 실제 코드처럼 papers는 상한 이하로 이미 잘려 있지만 total_count는 훨씬 크다.
        return search.SearchResult(papers=[], total_count=40000)

    monkeypatch.setattr(runner.search, "collect", fake_collect)

    await runner.advance(db, a)
    db.refresh(a)
    assert a.status == "failed"
    assert "40000" in a.error
    assert str(settings.max_papers_per_analysis) in a.error


async def test_do_search_passes_when_total_count_within_limit(ctx, monkeypatch):
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="searching", query_hash="h")
    db.add(a)
    db.commit()

    async def fake_collect(db, subfield, year_from, year_to, *, client):
        return search.SearchResult(papers=[], total_count=10)

    monkeypatch.setattr(runner.search, "collect", fake_collect)

    await runner.advance(db, a)
    db.refresh(a)
    assert a.status == "extracting"
    assert a.error is None


async def test_extract_defers_submit_when_concurrent_slot_limit_reached(ctx, monkeypatch):
    """C2: batch_job_id가 채워진(=진행 중인) analysis 수가 상한 이상이면 새 batch를
    제출하지 않고 다음 루프에서 재시도해야 한다."""
    db, sf = ctx
    for i in range(settings.batch_max_concurrent_jobs):
        db.add(Analysis(subfield_id=sf.id, year=2020 + i, status="extracting",
                        query_hash="h", batch_job_id=f"running-{i}"))
    db.commit()

    a = Analysis(subfield_id=sf.id, year=2025, status="extracting", query_hash="h")
    db.add(a)
    db.commit()

    p = Paper(paper_key="k1", title="T", abstract="A", year=2025, source="openalex",
              korea_flag=True)
    db.add(p)
    db.commit()
    db.add(AnalysisPaper(analysis_id=a.id, paper_id=p.id))
    db.commit()

    calls = {"n": 0}

    async def fake_submit_async(requests):
        calls["n"] += 1
        return "job-should-not-happen"

    monkeypatch.setattr(runner.gemini_batch, "submit_async", fake_submit_async)

    await runner.advance(db, a)
    db.refresh(a)

    assert calls["n"] == 0
    assert a.batch_job_id is None
    assert a.status == "extracting"  # 대기 — 다음 루프 주기에 재시도


async def test_extract_submits_when_slot_available(ctx, monkeypatch):
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="extracting", query_hash="h")
    db.add(a)
    db.commit()

    p = Paper(paper_key="k1", title="T", abstract="A", year=2025, source="openalex",
              korea_flag=True)
    db.add(p)
    db.commit()
    db.add(AnalysisPaper(analysis_id=a.id, paper_id=p.id))
    db.commit()

    seen = {}

    async def fake_submit_async(requests, analysis_id=None):
        seen["analysis_id"] = analysis_id
        return "job-ok"

    monkeypatch.setattr(runner.gemini_batch, "submit_async", fake_submit_async)

    await runner.advance(db, a)
    db.refresh(a)
    assert a.batch_job_id == "job-ok"
    # M17: 잡↔분석 역추적을 위해 analysis id가 submit_async까지 전달돼야 한다.
    assert seen["analysis_id"] == a.id


async def test_search_attempts_increments_on_transient_rate_limit(ctx, monkeypatch):
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="searching", query_hash="h")
    db.add(a)
    db.commit()

    async def fake_collect(*args, **kwargs):
        raise RateLimited("OpenAlex 429 재시도 소진", permanent=False)

    monkeypatch.setattr(runner.search, "collect", fake_collect)

    await runner.advance(db, a)
    db.refresh(a)
    assert a.search_attempts == 1
    assert a.status == "searching"  # 아직 상한 미도달 — 다음 루프에서 재시도


async def test_search_attempts_fails_after_max_and_resets_on_success(ctx, monkeypatch):
    """I9: 검색 단계도 extract_attempts와 대칭으로 시도 상한을 둬야, 이미 재시도를
    소진한 비영구 429가 30초 간격으로 무한히 재과금하며 도는 것을 막을 수 있다."""
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="searching", query_hash="h")
    db.add(a)
    db.commit()

    async def fake_collect_fails(*args, **kwargs):
        raise RateLimited("OpenAlex 429 재시도 소진", permanent=False)

    monkeypatch.setattr(runner.search, "collect", fake_collect_fails)

    for _ in range(settings.max_search_attempts):
        await runner.advance(db, a)
        db.refresh(a)

    assert a.status == "failed"
    assert a.search_attempts == settings.max_search_attempts
    assert a.error is not None

    # 성공하면 리셋되어야 한다(재시도 경로 확인용으로 별도 analysis 사용).
    a2 = Analysis(subfield_id=sf.id, year=2026, status="searching", query_hash="h",
                  search_attempts=2)
    db.add(a2)
    db.commit()

    async def fake_collect_succeeds(db, subfield, year_from, year_to, *, client):
        return search.SearchResult(papers=[], total_count=0)

    monkeypatch.setattr(runner.search, "collect", fake_collect_succeeds)
    await runner.advance(db, a2)
    db.refresh(a2)
    assert a2.search_attempts == 0
    assert a2.status == "extracting"


async def test_do_search_searched_count_is_cumulative_not_just_latest(ctx, monkeypatch):
    """I7: searched_count는 이번 검색 결과 건수가 아니라 AnalysisPaper 누적 링크 수여야
    stats.compute(_analysis_papers 기준)와 같은 값을 낸다. 검색식을 좁혀 재실행해도(이번
    결과가 더 적어도) 과거에 걸렸던 논문은 증분 정책상 계속 링크돼 있어야 한다."""
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="searching", query_hash="h")
    db.add(a)
    db.commit()

    async def fake_collect_first(db, subfield, year_from, year_to, *, client):
        return search.SearchResult(
            papers=[_search_paper("k1"), _search_paper("k2")], total_count=2
        )

    monkeypatch.setattr(runner.search, "collect", fake_collect_first)
    await runner.advance(db, a)
    db.refresh(a)
    assert a.searched_count == 2

    a.status = "searching"
    db.commit()

    async def fake_collect_second(db, subfield, year_from, year_to, *, client):
        # 검색식이 좁아져 이번 결과는 1건뿐이라고 가정.
        return search.SearchResult(papers=[_search_paper("k1")], total_count=1)

    monkeypatch.setattr(runner.search, "collect", fake_collect_second)
    await runner.advance(db, a)
    db.refresh(a)
    # k1, k2 모두 여전히 링크돼 있어야 한다(프리즈 없는 증분 갱신 정책).
    assert a.searched_count == 2
    linked = {r.paper_id for r in db.query(AnalysisPaper.paper_id).filter(
        AnalysisPaper.analysis_id == a.id
    )}
    assert len(linked) == 2


def test_enqueue_clears_analysis_papers_when_query_changed(ctx):
    """I7: 검색식이 바뀌어(query_hash 불일치) 되살아나는 경우, 옛 검색식으로만 걸리던
    논문이 모집단에 영구히 남지 않도록 기존 AnalysisPaper 링크를 비워야 한다."""
    db, sf = ctx
    first = runner.enqueue(db, sf, 2025, 2025, force=False)[0]
    p = Paper(paper_key="old", title="T", abstract="A", year=2025, source="openalex",
               korea_flag=True)
    db.add(p)
    db.commit()
    db.add(AnalysisPaper(analysis_id=first.id, paper_id=p.id))
    first.status = "done"
    first.searched_count = 1
    db.commit()

    sf.query = "quantum error correction"  # 검색식 변경 → query_hash 불일치
    db.commit()

    again = runner.enqueue(db, sf, 2025, 2025, force=False)
    assert len(again) == 1
    assert db.query(AnalysisPaper).filter(
        AnalysisPaper.analysis_id == again[0].id
    ).count() == 0
    # papers 테이블 자체와 캐시 자산은 건드리지 않는다.
    assert db.query(Paper).filter(Paper.paper_key == "old").count() == 1


def test_enqueue_keeps_analysis_papers_when_force_rerun_with_same_query(ctx):
    """검색식이 그대로인데 force로 재실행하는 경우는 증분 갱신이므로 기존 링크를
    지우면 안 된다(I7의 정리는 query_hash 불일치 상황에만 적용)."""
    db, sf = ctx
    first = runner.enqueue(db, sf, 2025, 2025, force=False)[0]
    p = Paper(paper_key="old", title="T", abstract="A", year=2025, source="openalex",
               korea_flag=True)
    db.add(p)
    db.commit()
    db.add(AnalysisPaper(analysis_id=first.id, paper_id=p.id))
    first.status = "done"
    db.commit()

    again = runner.enqueue(db, sf, 2025, 2025, force=True)
    assert len(again) == 1
    assert db.query(AnalysisPaper).filter(
        AnalysisPaper.analysis_id == again[0].id
    ).count() == 1


def test_enqueue_resets_extract_and_search_attempts_on_revival(ctx):
    """M11: 상한에 걸려 failed된 잡을 재실행할 때 카운터를 리셋하지 않으면, 되살아난
    잡이 첫 poll에서 진행이 없기만 해도 즉시 다시 failed로 떨어진다."""
    db, sf = ctx
    first = runner.enqueue(db, sf, 2025, 2025, force=False)[0]
    first.status = "failed"
    first.extract_attempts = settings.max_extract_attempts
    first.search_attempts = settings.max_search_attempts
    db.commit()

    again = runner.enqueue(db, sf, 2025, 2025, force=False)
    assert len(again) == 1
    assert again[0].extract_attempts == 0
    assert again[0].search_attempts == 0
