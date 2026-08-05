from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from app.config import settings
from app.models.analysis import Analysis
from app.models.schedule import ScheduledRun
from app.services import runner

KST = ZoneInfo(settings.schedule_timezone)


def test_run_scheduled_skips_when_not_due_hour(ctx):
    db, sf = ctx
    off_hour = datetime(2026, 8, 10, 14, 0, tzinfo=KST)  # 10일이지만 03시대가 아님
    assert runner.run_scheduled_if_due(db, now=off_hour) is None
    assert db.query(Analysis).count() == 0
    assert db.query(ScheduledRun).count() == 0


def test_run_scheduled_skips_when_not_due_day(ctx):
    db, sf = ctx
    off_day = datetime(2026, 8, 15, 3, 0, tzinfo=KST)  # 03시대이지만 10일이 아님
    assert runner.run_scheduled_if_due(db, now=off_day) is None
    assert db.query(Analysis).count() == 0


def test_run_scheduled_queues_when_due(ctx):
    db, sf = ctx
    due = datetime(2026, 8, 10, 3, 30, tzinfo=KST)
    queued = runner.run_scheduled_if_due(db, now=due)
    # schedule_years_back 기본값 1 → 당해(2026)·직전(2025) 연도, 세부기술 1개 → 2건
    assert queued == 2
    years = {a.year for a in db.query(Analysis).all()}
    assert years == {2025, 2026}
    assert all(a.trigger == "scheduled" for a in db.query(Analysis).all())
    assert db.query(ScheduledRun).count() == 1
    assert db.query(ScheduledRun).first().queued_count == 2


def test_run_scheduled_idempotent_within_same_month(ctx):
    """컨테이너가 실행 시각대에 재시작돼 루프가 다시 돌아도 두 번 큐잉되면 안 된다."""
    db, sf = ctx
    first = runner.run_scheduled_if_due(db, now=datetime(2026, 8, 10, 3, 5, tzinfo=KST))
    assert first == 2

    second = runner.run_scheduled_if_due(db, now=datetime(2026, 8, 10, 3, 40, tzinfo=KST))
    assert second is None
    assert db.query(ScheduledRun).count() == 1
    assert db.query(Analysis).count() == 2  # 중복 생성 없음


def test_run_scheduled_next_month_queues_again(ctx):
    """같은 달만 막히고, 다음 달은 정상적으로 다시 큐잉돼야 한다."""
    db, sf = ctx
    runner.run_scheduled_if_due(db, now=datetime(2026, 8, 10, 3, 0, tzinfo=KST))
    third = runner.run_scheduled_if_due(db, now=datetime(2026, 9, 10, 3, 0, tzinfo=KST))
    assert third == 2  # 8월에 done되지 않았으므로 여전히 active 상태 재확인 대상
    assert db.query(ScheduledRun).count() == 2


def test_run_scheduled_ignores_inactive_subfields(ctx):
    db, sf = ctx
    sf.active = False
    db.commit()

    queued = runner.run_scheduled_if_due(db, now=datetime(2026, 8, 10, 3, 0, tzinfo=KST))
    assert queued == 0
    assert db.query(Analysis).count() == 0


def test_next_scheduled_run_at_stays_in_month_before_due_hour(ctx):
    db, sf = ctx
    before = datetime(2026, 8, 5, 0, 0, tzinfo=KST)
    nxt = runner.next_scheduled_run_at(db, now=before)
    assert (nxt.year, nxt.month, nxt.day, nxt.hour) == (2026, 8, 10, 3)


def test_next_scheduled_run_at_rolls_to_next_month_after_due_hour(ctx):
    db, sf = ctx
    after = datetime(2026, 8, 10, 4, 0, tzinfo=KST)  # 실행 시각(03시대)을 이미 지남
    nxt = runner.next_scheduled_run_at(db, now=after)
    assert (nxt.year, nxt.month, nxt.day, nxt.hour) == (2026, 9, 10, 3)


def test_next_scheduled_run_at_rolls_year_over_december(ctx):
    db, sf = ctx
    dec = datetime(2026, 12, 15, 0, 0, tzinfo=KST)
    nxt = runner.next_scheduled_run_at(db, now=dec)
    assert (nxt.year, nxt.month, nxt.day) == (2027, 1, 10)


def _set_schedule(db, **kwargs) -> None:
    cfg = runner.get_schedule_settings(db)
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    db.commit()


def test_run_scheduled_follows_db_day_setting(ctx):
    """PUT /admin/schedule로 day를 바꾸면 .env 기본값(10일)이 아니라 새 값을 따라야 한다."""
    db, sf = ctx
    _set_schedule(db, day=15)

    old_day_due = datetime(2026, 8, 10, 3, 30, tzinfo=KST)  # 옛 기본값(10일)엔 더 이상 안 돈다
    assert runner.run_scheduled_if_due(db, now=old_day_due) is None
    assert db.query(Analysis).count() == 0

    new_day_due = datetime(2026, 8, 15, 3, 30, tzinfo=KST)
    queued = runner.run_scheduled_if_due(db, now=new_day_due)
    assert queued == 2


def test_run_scheduled_respects_disabled_flag_from_db(ctx):
    db, sf = ctx
    _set_schedule(db, enabled=False)
    due = datetime(2026, 8, 10, 3, 30, tzinfo=KST)
    assert runner.run_scheduled_if_due(db, now=due) is None
    assert db.query(Analysis).count() == 0


def test_run_scheduled_now_queues_outside_due_hour(ctx):
    """run-now는 스케줄 시각(day/hour) 판정을 우회해 언제든 큐잉해야 한다."""
    db, sf = ctx
    off_hour = datetime(2026, 8, 10, 14, 0, tzinfo=KST)
    queued = runner.run_scheduled_now(db, now=off_hour)
    assert queued == 2
    assert db.query(ScheduledRun).first().trigger == "manual"


def test_run_scheduled_now_does_not_block_monthly_run(ctx):
    """수동 '지금 실행'이 그 달의 정기 실행 멱등성 키(run_month="YYYY-MM")를 막으면 안 된다."""
    db, sf = ctx
    manual_time = datetime(2026, 8, 10, 1, 0, tzinfo=KST)  # 정기 실행 시각 이전에 먼저 수동 실행
    assert runner.run_scheduled_now(db, now=manual_time) == 2

    due = datetime(2026, 8, 10, 3, 30, tzinfo=KST)
    assert runner.run_scheduled_if_due(db, now=due) == 2  # 수동 실행 이후에도 정기 실행은 정상 큐잉

    runs = db.query(ScheduledRun).order_by(ScheduledRun.ran_at).all()
    assert len(runs) == 2
    assert runs[0].trigger == "manual"
    assert runs[1].trigger == "scheduled"
    assert runs[0].run_month != runs[1].run_month
    assert runs[1].run_month == "2026-08"


def test_run_scheduled_requeues_already_done_analysis(ctx):
    """스케줄러의 존재 이유 — 이미 done인 분석도 매달 다시 돌려 신규 논문을 잡아야 한다.

    enqueue()는 status="done"이고 query_hash가 그대로면 아무것도 하지 않으므로,
    스케줄러가 force=False로 부르면 완료된 세부기술이 영영 갱신되지 않는다.
    이 테스트가 깨지면 스케줄러가 조용히 무력화된 것이다.
    """
    db, sf = ctx
    from app.services import search

    year = 2026
    done = Analysis(
        subfield_id=sf.id,
        year=year,
        status="done",
        query_hash=search.query_hash(sf, year, year),  # 검색식 변경 없음
        report_md="기존 보고서",
        analyzed_count=10,
    )
    db.add(done)
    db.commit()

    due = datetime(2026, 8, 10, 3, 30, tzinfo=KST)
    queued = runner.run_scheduled_if_due(db, now=due)

    assert queued == 2  # 2026(기존 done 되살림) + 2025(신규)
    db.refresh(done)
    assert done.status == "pending", "done 분석이 다시 큐잉되지 않으면 신규 논문을 못 잡는다"
    assert done.trigger == "scheduled"
    assert done.report_md == "기존 보고서", "보고서는 유지되어야 한다(신규 0건이면 재생성 생략)"


def test_scheduler_queues_every_configured_country(ctx):
    """schedule_settings.countries는 콤마 구분 목록이다. 기본 KR이라 켜기 전에는
    현행과 같고, 국가마다 검색·추출이 따로 돌아 비용이 곱해진다."""
    db, sf = ctx
    cfg = runner.get_schedule_settings(db)
    cfg.countries = "KR,US"
    cfg.years_back = 0
    db.commit()

    runner.run_scheduled_now(db, now=datetime(2026, 8, 2, 3, 0))

    rows = db.query(Analysis).all()
    assert {a.country for a in rows} == {"KR", "US"}
    assert len(rows) == 2


def test_scheduler_defaults_to_kr_only(ctx):
    db, sf = ctx
    cfg = runner.get_schedule_settings(db)
    cfg.years_back = 0
    db.commit()

    runner.run_scheduled_now(db, now=datetime(2026, 8, 2, 3, 0))
    assert {a.country for a in db.query(Analysis).all()} == {"KR"}
