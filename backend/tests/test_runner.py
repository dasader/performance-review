import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app.models.analysis import Analysis, AnalysisPaper
from app.models.field import Field, Subfield
from app.models.paper import Paper
from app.services import budget, runner


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
    db.commit()
    return db, sf


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
