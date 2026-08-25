import pytest
from app.clients._http import RateLimited
from app.config import settings
from app.models.analysis import Analysis, AnalysisPaper
from app.models.paper import Paper, PaperExtraction
from app.models.schedule import AnalysisRun
from app.services import budget, mapper, runner, search


def _search_paper(key, **kw):
    base = {"paper_key": key, "title": "T", "abstract": "A", "year": 2025, "journal": None,
            "doi": None, "authors": [], "institutions": [], "countries": [],
            "citations": 0, "source": "openalex", "lead_countries": []}
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


def test_resume_paused_stays_paused_when_remainder_cannot_fund_one_analysis(ctx):
    """한도를 아직 넘지 않았어도 가장 싼 분석 한 건조차 못 치를 잔액이면 두지 않는다.

    실측(2026-08-24): 사용액이 $0.4990/$0.50에서 멈추자 게이트(`>= 한도`)를 계속
    통과해 25건이 30초마다 paused→pending→paused를 오갔다(로그 1,003회). 그 각각이
    check_budget보다 먼저 도는 count_only를 실제로 한 번씩 호출하는데, 게이트에서
    막히면 record_usage까지 못 가 그 비용이 기록되지도 않는다. 그리고 UTC 자정에
    예산이 리셋되는 순간 그 25건이 한꺼번에 OpenAlex로 쏟아진 것이 504 무더기의
    방아쇠였다.
    """
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="paused", query_hash="h", error="msg")
    db.add(a)
    db.commit()
    # 한도까지 $0.001 남았다 — count 1콜 + 최소 1페이지($0.002)에 못 미친다.
    budget.record_usage(db, settings.openalex_daily_budget_usd - 0.001, None)

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

    p = Paper(paper_key="k1", title="T", abstract="A", year=2025, source="openalex")
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


async def test_extracted_this_run_accumulates_across_multiple_batches(ctx, monkeypatch):
    """M18: 추출은 batch_max_requests_per_file 단위로 여러 청크로 쪼개져 여러 루프
    틱에 걸쳐 저장될 수 있다 — extracted_this_run이 메모리 변수가 아니라 DB 컬럼이어야
    하는 이유의 회귀 테스트. 첫 청크에서 p1만 저장되고(p2는 남음), 다음 루프 틱(다음
    batch 결과 도착)에서 p2가 저장되는 상황을 흉내낸다."""
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="extracting", query_hash="h",
                 batch_job_id="job-1")
    db.add(a)
    db.commit()

    p1 = Paper(paper_key="k1", title="T1", abstract="A1", year=2025, source="openalex")
    p2 = Paper(paper_key="k2", title="T2", abstract="A2", year=2025, source="openalex")
    db.add_all([p1, p2])
    db.commit()
    db.add(AnalysisPaper(analysis_id=a.id, paper_id=p1.id))
    db.add(AnalysisPaper(analysis_id=a.id, paper_id=p2.id))
    db.commit()

    monkeypatch.setattr(runner.gemini_batch, "poll", lambda job_id: ("succeeded", [{"key": "k1"}]))

    # 청크 1: before=[p1,p2](2건), 저장 후 still_pending=[p2](1건) — p1만 이번에 저장됨.
    calls = {"n": 0}

    def fake_pending_chunk1(db, analysis, papers):
        calls["n"] += 1
        return [p1, p2] if calls["n"] == 1 else [p2]

    monkeypatch.setattr(runner.mapper, "pending_papers", fake_pending_chunk1)
    monkeypatch.setattr(runner.mapper, "save_results", lambda db, a, results: 1)

    await runner.advance(db, a)
    db.refresh(a)
    assert a.extracted_this_run == 1
    assert a.status == "extracting"  # p2가 아직 남아 다음 청크를 기다림
    assert a.batch_job_id is None

    # 다음 루프 틱: 두 번째 청크가 이미 제출·완료됐다고 흉내(제출 로직은 다른 테스트가
    # 커버) — batch_job_id를 직접 채워 폴링 경로로 들어가게 한다.
    a.batch_job_id = "job-2"
    db.commit()
    monkeypatch.setattr(runner.gemini_batch, "poll", lambda job_id: ("succeeded", [{"key": "k2"}]))

    # 청크 2: before=[p2](1건), 저장 후 still_pending=[](0건) — 이제 다 끝남.
    calls2 = {"n": 0}

    def fake_pending_chunk2(db, analysis, papers):
        calls2["n"] += 1
        return [p2] if calls2["n"] == 1 else []

    monkeypatch.setattr(runner.mapper, "pending_papers", fake_pending_chunk2)
    monkeypatch.setattr(runner.mapper, "save_results", lambda db, a, results: 1)

    await runner.advance(db, a)
    db.refresh(a)
    assert a.extracted_this_run == 2  # 두 청크의 저장 건수(1+1)가 합산 누적됨
    assert a.status == "reducing"


async def test_extract_attempts_fails_after_max_when_no_progress(ctx, monkeypatch):
    """파싱 불가 논문 때문에 pending 건수가 줄지 않는 상황이 max_extract_attempts회
    반복되면 무한 재제출 대신 failed로 전환돼야 한다."""
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="extracting", query_hash="h")
    db.add(a)
    db.commit()

    p = Paper(paper_key="k1", title="T", abstract="A", year=2025, source="openalex")
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

    p = Paper(paper_key="k1", title="T", abstract="A", year=2025, source="openalex")
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

    p = Paper(paper_key="k1", title="T", abstract="A", year=2025, source="openalex")
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

    async def fake_collect_succeeds(db, subfield, year_from, year_to, *, client, country="KR"):
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

    async def fake_collect_first(db, subfield, year_from, year_to, *, client, country="KR"):
        return search.SearchResult(
            papers=[_search_paper("k1"), _search_paper("k2")], total_count=2
        )

    monkeypatch.setattr(runner.search, "collect", fake_collect_first)
    await runner.advance(db, a)
    db.refresh(a)
    assert a.searched_count == 2

    a.status = "searching"
    db.commit()

    async def fake_collect_second(db, subfield, year_from, year_to, *, client, country="KR"):
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
    p = Paper(paper_key="old", title="T", abstract="A", year=2025, source="openalex")
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
    p = Paper(paper_key="old", title="T", abstract="A", year=2025, source="openalex")
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


def test_enqueue_resets_extracted_this_run_on_revival(ctx):
    """M18: 지난 실행에서 누적된 extracted_this_run이 이번 AnalysisRun.new_papers에
    섞여 들어가면 안 된다 — enqueue()가 되살릴 때 0으로 리셋해야 한다."""
    db, sf = ctx
    first = runner.enqueue(db, sf, 2025, 2025, force=False)[0]
    first.status = "done"
    first.extracted_this_run = 7  # 지난 실행에서 남은 값
    db.commit()

    again = runner.enqueue(db, sf, 2025, 2025, force=True)
    assert again[0].extracted_this_run == 0


# ── C4: 신규 추출 0건이면 reduce_subfield(LLM) 호출을 생략 ──

def _reducing_analysis(db, sf, *, report_md=None, analyzed_count=0, report_model_ver=None,
                       trigger="manual"):
    a = Analysis(subfield_id=sf.id, year=2025, status="reducing", query_hash="h",
                 report_md=report_md, analyzed_count=analyzed_count,
                 report_model_ver=report_model_ver, trigger=trigger)
    db.add(a)
    db.commit()
    return a


def _link_extracted_paper(db, a, sf, key, *, model_ver=None):
    p = Paper(paper_key=key, title="T", abstract="A", year=2025, source="openalex")
    db.add(p)
    db.commit()
    db.add(AnalysisPaper(analysis_id=a.id, paper_id=p.id))
    db.add(PaperExtraction(paper_key=key, subfield_id=sf.id, tech_summary="s",
                           model_ver=model_ver or runner.mapper.model_ver()))
    db.commit()


async def test_do_reduce_skips_llm_when_no_new_extractions(ctx, monkeypatch):
    db, sf = ctx
    a = _reducing_analysis(db, sf, report_md="기존 보고서", analyzed_count=1,
                           report_model_ver=runner.mapper.model_ver())
    # extracted_this_run 기본값 0 — 이번 실행에서 _do_extract가 아무것도 저장하지 않았음
    _link_extracted_paper(db, a, sf, "k1")

    called = {"n": 0}

    async def fake_reduce(*args, **kwargs):
        called["n"] += 1
        return "새 보고서", []

    monkeypatch.setattr(runner.reducer, "reduce_subfield", fake_reduce)

    await runner.advance(db, a)
    db.refresh(a)
    assert called["n"] == 0
    assert a.report_md == "기존 보고서"  # 그대로 유지
    assert a.status == "done"
    assert a.analyzed_count == 1  # 통계 근거는 여전히 갱신됨

    runs = db.query(AnalysisRun).filter(AnalysisRun.analysis_id == a.id).all()
    assert runs[0].new_papers == 0  # 신규 추출 0건이면 new_papers도 0


async def test_do_reduce_calls_llm_when_extractions_increased(ctx, monkeypatch):
    db, sf = ctx
    a = _reducing_analysis(db, sf, report_md="기존 보고서", analyzed_count=0,
                           report_model_ver=runner.mapper.model_ver())
    _link_extracted_paper(db, a, sf, "k1")  # analyzed_count(0) < 이번 추출 건수(1)

    called = {"n": 0}

    async def fake_reduce(*args, **kwargs):
        called["n"] += 1
        return "새 보고서", []

    monkeypatch.setattr(runner.reducer, "reduce_subfield", fake_reduce)

    await runner.advance(db, a)
    db.refresh(a)
    assert called["n"] == 1
    assert a.report_md == "새 보고서"


async def test_do_reduce_generates_when_report_md_missing_even_if_no_new(ctx, monkeypatch):
    db, sf = ctx
    a = _reducing_analysis(db, sf, report_md=None, analyzed_count=0)  # 추출 0건, 보고서 없음

    called = {"n": 0}

    async def fake_reduce(*args, **kwargs):
        called["n"] += 1
        return "최초 보고서", []

    monkeypatch.setattr(runner.reducer, "reduce_subfield", fake_reduce)

    await runner.advance(db, a)
    db.refresh(a)
    assert called["n"] == 1
    assert a.report_md == "최초 보고서"


async def test_do_reduce_regenerates_when_model_ver_changed_even_if_count_same(ctx, monkeypatch):
    """model_ver가 바뀌면 같은 논문 집합이 전량 재추출된 것이므로, 추출 건수가
    이전과 같아도 report_model_ver 불일치로 반드시 재생성해야 한다."""
    db, sf = ctx
    a = _reducing_analysis(db, sf, report_md="옛 모델 보고서", analyzed_count=1,
                           report_model_ver="old-model/low/v1")
    a.extracted_this_run = 1  # _do_extract가 이번 실행에서 1건을 실제로 재추출해 누적한 값
    db.commit()
    _link_extracted_paper(db, a, sf, "k1")  # 현재 model_ver로 추출된 1건 — 건수는 그대로

    called = {"n": 0}

    async def fake_reduce(*args, **kwargs):
        called["n"] += 1
        return "재생성된 보고서", []

    monkeypatch.setattr(runner.reducer, "reduce_subfield", fake_reduce)

    await runner.advance(db, a)
    db.refresh(a)
    assert called["n"] == 1
    assert a.report_md == "재생성된 보고서"
    assert a.report_model_ver == runner.mapper.model_ver()

    # M18 회귀 테스트: 총계 차이(1-1=0)가 아니라 실제 추출 건수(1)가 기록돼야 한다.
    # 실측 버그(analysis 4, 양자컴퓨팅 2026): 스키마 v1→v2로 10건 전량 재추출됐는데
    # new_papers=0으로 기록된 사례가 이 시나리오다.
    runs = db.query(AnalysisRun).filter(AnalysisRun.analysis_id == a.id).all()
    assert runs[0].new_papers == 1


async def test_do_reduce_records_analysis_run(ctx, monkeypatch):
    db, sf = ctx
    a = _reducing_analysis(db, sf, report_md=None, analyzed_count=0, trigger="scheduled")
    a.searched_count = 3
    a.extracted_this_run = 1  # _do_extract가 이번 실행에서 누적했을 값을 흉내(reduce만 검증)
    db.commit()
    _link_extracted_paper(db, a, sf, "k1")

    async def fake_reduce(*args, **kwargs):
        return "보고서", []

    monkeypatch.setattr(runner.reducer, "reduce_subfield", fake_reduce)

    await runner.advance(db, a)

    runs = db.query(AnalysisRun).filter(AnalysisRun.analysis_id == a.id).all()
    assert len(runs) == 1
    assert runs[0].trigger == "scheduled"
    assert runs[0].new_papers == 1
    assert runs[0].searched_count == 3
    assert runs[0].analyzed_count == 1


async def test_permanent_rate_limit_pauses_instead_of_blocking_the_loop(ctx, monkeypatch):
    """영구 429는 잡을 paused로 내려 루프가 계속 돌게 한다.

    loop()가 활성 분석을 순차로 await하므로, 한 분석이 대기하면 나머지 분석과
    batch 폴링·resume_paused까지 함께 멈춘다. 실측(2026-08-01): OpenAlex가
    Retry-After 43,579초를 반환해 재추출 110건이 통째로 정지했다.
    """
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="searching", query_hash="h",
                 search_attempts=0)
    db.add(a)
    db.commit()

    async def fake_collect(*args, **kwargs):
        raise RateLimited("OpenAlex 일일 크레딧 소진", permanent=True)

    monkeypatch.setattr(runner.search, "collect", fake_collect)

    await runner.advance(db, a)
    db.refresh(a)
    assert a.status == "paused"
    # 소진은 이 잡의 잘못이 아니므로 실패 카운터를 올리지 않는다 — 올리면 예산이
    # 회복돼 재개된 뒤에도 상한에 걸려 failed로 떨어진다.
    assert a.search_attempts == 0


async def test_force_requeue_preserves_inflight_batch_job(ctx):
    """C5: batch 진행 중인 분석은 force 재실행이 건드리지 않는다.

    batch_job_id를 비우면 Gemini에서 이미 돌고 있는(=과금되는) 잡의 핸들을 잃고
    같은 논문을 통째로 재제출한다 — 청크당 최대 1,000건이라 손실이 크다.
    관리자가 "지금 실행"을 누르는 시점을 통제할 수 없으므로 코드로 막는다.
    """
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="extracting",
                 query_hash=search.query_hash(sf, 2025, 2025),
                 batch_job_id="batches/inflight", extract_attempts=2,
                 extracted_this_run=17)
    db.add(a)
    db.commit()

    runner.enqueue(db, sf, 2025, 2025, force=True, trigger="manual")
    db.refresh(a)

    assert a.batch_job_id == "batches/inflight"
    assert a.status == "extracting"
    # 카운터도 그대로여야 한다 — 리셋하면 이번 실행의 추출 집계가 어긋난다.
    assert a.extract_attempts == 2
    assert a.extracted_this_run == 17


async def test_force_requeue_clears_batch_job_when_batch_failed(ctx):
    """batch가 실패해 failed로 떨어진 행은 죽은 잡이므로 핸들을 비우고 재시작한다.
    ACTIVE_STATES 밖이라 위 보호 분기에 걸리지 않아야 한다."""
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="failed",
                 query_hash=search.query_hash(sf, 2025, 2025),
                 batch_job_id="batches/dead", error="Gemini batch 작업 실패")
    db.add(a)
    db.commit()

    runner.enqueue(db, sf, 2025, 2025, force=True, trigger="manual")
    db.refresh(a)

    assert a.batch_job_id is None
    assert a.status == "pending"
    assert a.error is None


async def test_force_requeue_still_resets_when_no_batch_inflight(ctx):
    """batch가 없으면 기존 force 동작(pending으로 되돌리기)이 그대로 유지된다."""
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="paused",
                 query_hash=search.query_hash(sf, 2025, 2025),
                 error="OpenAlex 일일 크레딧 소진 — 내일 자동 재개됩니다.",
                 search_attempts=1)
    db.add(a)
    db.commit()

    runner.enqueue(db, sf, 2025, 2025, force=True, trigger="manual")
    db.refresh(a)

    assert a.status == "pending"
    assert a.search_attempts == 0
    assert a.error is None


async def test_do_reduce_preserves_sections_when_skipping_regeneration(ctx, monkeypatch):
    """신규 추출이 없어 보고서 재생성을 건너뛸 때 기존 세부 보고서를 지우면 안 된다.

    지우면 "재생성을 생략했는데 화면 내용이 줄어드는" 결과가 된다.
    """
    db, sf = ctx
    kept = [{"name": "알고리즘", "body": "이전 부분보고서"}]
    a = Analysis(subfield_id=sf.id, year=2025, status="reducing", query_hash="h",
                 report_md="기존 보고서", analyzed_count=0,
                 report_model_ver=mapper.model_ver(), sections_json=kept)
    db.add(a)
    db.commit()

    async def fail(*args, **kwargs):
        raise AssertionError("재생성을 건너뛰어야 한다")

    monkeypatch.setattr(runner.reducer, "reduce_subfield", fail)

    await runner.advance(db, a)
    db.refresh(a)
    assert a.sections_json == kept
    assert a.report_md == "기존 보고서"


def test_enqueue_creates_separate_rows_per_country(ctx):
    db, sf = ctx
    kr = runner.enqueue(db, sf, 2025, 2025, force=False, country="KR")
    us = runner.enqueue(db, sf, 2025, 2025, force=False, country="US")
    assert kr[0].id != us[0].id
    assert {a.country for a in kr + us} == {"KR", "US"}
    assert db.query(Analysis).count() == 2


def test_enqueue_defaults_to_kr(ctx):
    db, sf = ctx
    assert runner.enqueue(db, sf, 2025, 2025, force=False)[0].country == "KR"


async def test_oversized_search_is_sampled_not_rejected(ctx, monkeypatch):
    """상한 초과를 거부하면 CN 11개·US 3개 세부기술이 그냥 실패한다(실측).
    거부 대신 인용 상위 N건을 수집하고 표본임을 stats에 남긴다."""
    db, sf = ctx
    a = Analysis(subfield_id=sf.id, year=2025, status="searching", query_hash="h")
    db.add(a)
    db.commit()

    async def fake_collect(*args, **kwargs):
        return search.SearchResult(papers=[_search_paper("k1")], total_count=25466)

    monkeypatch.setattr(runner.search, "collect", fake_collect)

    await runner.advance(db, a)
    db.refresh(a)
    assert a.status == "extracting"
    assert a.error is None
    assert a.stats_json["population_total"] == 25466


def test_analysis_too_large_is_gone():
    """상한 초과를 거부하던 하드 가드는 표본 수집으로 대체됐다."""
    assert not hasattr(runner, "AnalysisTooLarge")


async def test_loop_advances_analyses_before_reports(ctx, monkeypatch):
    """보고서 합성이 분석 파이프라인을 막지 않아야 한다.

    비교 하나가 쌍별 포함 최대 2분 걸리는데, 그것이 분석 루프보다 먼저 돌면
    그 틱의 세부기술 진행이 통째로 밀린다. 보고서는 파이프라인보다 우선이 아니다.
    """
    order: list[str] = []

    async def fake_reports(db):
        order.append("reports")

    async def fake_advance(db, analysis):
        order.append("analysis")

    monkeypatch.setattr(runner, "advance_field_reports", fake_reports)
    monkeypatch.setattr(runner, "advance", fake_advance)
    monkeypatch.setattr(runner, "run_scheduled_if_due", lambda db: None)
    monkeypatch.setattr(runner, "resume_paused", lambda db: None)

    db, sf = ctx
    db.add(Analysis(subfield_id=sf.id, year=2026, country="KR", status="pending",
                    query_hash="h"))
    db.commit()
    await runner._tick(db)

    assert order == ["analysis", "reports"]
