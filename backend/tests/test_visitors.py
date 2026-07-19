from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.field import Field, Subfield
from app.models.visit import Visit
from app.services import visitors as visitors_service


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return TestingSession


@pytest.fixture
def client(db_session):
    db = db_session()
    f = Field(name="양자", slug="quantum", order_no=1)
    db.add(f)
    db.flush()
    db.add(Subfield(field_id=f.id, name="양자컴퓨팅", query="quantum computing"))
    db.commit()
    db.close()

    app.dependency_overrides[get_db] = lambda: db_session()
    yield TestClient(app), db_session
    app.dependency_overrides.clear()


def test_record_visit_dedupes_same_day(db_session):
    db = db_session()
    visitors_service.record_visit(db, "1.2.3.4", "ua-a")
    visitors_service.record_visit(db, "1.2.3.4", "ua-a")
    assert db.query(Visit).count() == 1
    db.close()


def test_record_visit_counts_separately_across_days(db_session, monkeypatch):
    db = db_session()
    day1 = date(2026, 7, 13)
    day2 = date(2026, 7, 14)

    monkeypatch.setattr(visitors_service, "_today", lambda: day1)
    visitors_service.record_visit(db, "1.2.3.4", "ua-a")
    monkeypatch.setattr(visitors_service, "_today", lambda: day2)
    visitors_service.record_visit(db, "1.2.3.4", "ua-a")

    assert db.query(Visit).count() == 2
    db.close()


def test_visitor_stats_today_and_week(db_session, monkeypatch):
    db = db_session()
    monday = date(2026, 7, 13)
    tuesday = date(2026, 7, 14)
    today = date(2026, 7, 15)  # 수요일, 이번 주 월요일은 07-13

    db.add(Visit(usage_date=monday, visitor_hash="a"))
    db.add(Visit(usage_date=tuesday, visitor_hash="a"))  # 같은 방문자, 다른 날
    db.add(Visit(usage_date=tuesday, visitor_hash="b"))
    db.add(Visit(usage_date=today, visitor_hash="c"))
    db.commit()

    monkeypatch.setattr(visitors_service, "_today", lambda: today)
    stats = visitors_service.visitor_stats(db)

    assert stats["today"] == 1
    assert stats["this_week"] == 3  # a, b, c — 유니크 방문자 수
    assert stats["daily"] == [
        {"date": "2026-07-13", "count": 1},
        {"date": "2026-07-14", "count": 2},
        {"date": "2026-07-15", "count": 1},
    ]
    db.close()


def test_week_start_is_monday():
    # 2026-07-19는 일요일 → 그 주 월요일은 07-13
    sunday = date(2026, 7, 19)
    assert visitors_service.week_start(sunday) == date(2026, 7, 13)
    monday = date(2026, 7, 13)
    assert visitors_service.week_start(monday) == monday


def test_public_request_is_tracked(client):
    c, db_session = client
    r = c.get("/api/fields")
    assert r.status_code == 200

    db = db_session()
    assert db.query(Visit).count() == 1
    db.close()


def test_admin_request_is_not_tracked(client):
    c, db_session = client
    r = c.get("/api/admin/dashboard", headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 200

    db = db_session()
    assert db.query(Visit).count() == 0
    db.close()


def test_health_request_is_not_tracked(client):
    c, db_session = client
    r = c.get("/api/health")
    assert r.status_code == 200

    db = db_session()
    assert db.query(Visit).count() == 0
    db.close()


def test_visitors_endpoint_returns_expected_shape(client):
    c, _db_session = client
    # 미들웨어는 call_next 이후에 기록하므로 이 요청 자신은 자기 응답에 반영되지 않는다.
    # 앞선 공개 요청 하나를 먼저 보내 오늘 방문자를 만든 뒤 조회한다.
    c.get("/api/fields")

    r = c.get("/api/visitors")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"today", "this_week", "daily"}
    assert isinstance(body["daily"], list)
    assert body["today"] >= 1
