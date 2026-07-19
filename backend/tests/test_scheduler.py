from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app.models.analysis import Analysis
from app.models.field import Field, Subfield
from app.models.schedule import ScheduledRun
from app.services import runner

KST = ZoneInfo(settings.schedule_timezone)


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    f = Field(name="양자", slug="quantum", order_no=1)
    db.add(f)
    db.flush()
    sf = Subfield(field_id=f.id, name="양자컴퓨팅", query="quantum computing", active=True)
    db.add(sf)
    db.commit()
    return db, sf


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


def test_next_scheduled_run_at_stays_in_month_before_due_hour():
    before = datetime(2026, 8, 5, 0, 0, tzinfo=KST)
    nxt = runner.next_scheduled_run_at(now=before)
    assert (nxt.year, nxt.month, nxt.day, nxt.hour) == (2026, 8, 10, 3)


def test_next_scheduled_run_at_rolls_to_next_month_after_due_hour():
    after = datetime(2026, 8, 10, 4, 0, tzinfo=KST)  # 실행 시각(03시대)을 이미 지남
    nxt = runner.next_scheduled_run_at(now=after)
    assert (nxt.year, nxt.month, nxt.day, nxt.hour) == (2026, 9, 10, 3)


def test_next_scheduled_run_at_rolls_year_over_december():
    dec = datetime(2026, 12, 15, 0, 0, tzinfo=KST)
    nxt = runner.next_scheduled_run_at(now=dec)
    assert (nxt.year, nxt.month, nxt.day) == (2027, 1, 10)
