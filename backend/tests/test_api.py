from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.clients.openalex import OpenAlexResult
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.analysis import Analysis
from app.models.budget import OpenAlexUsage
from app.models.field import Field, Subfield
from app.routers import admin as admin_module


@pytest.fixture
def client():
    # StaticPool: FastAPI가 sync 엔드포인트를 워커 스레드풀에서 실행하므로, 스레드마다
    # 새 커넥션을 여는 기본 풀로는 :memory: sqlite가 매번 빈 DB로 보인다.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    db = TestingSession()
    f = Field(name="양자", slug="quantum", order_no=1)
    db.add(f)
    db.flush()
    db.add(Subfield(field_id=f.id, name="양자컴퓨팅", query="quantum computing"))
    db.commit()

    app.dependency_overrides[get_db] = lambda: TestingSession()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_public_fields_lists_subfields(client):
    r = client.get("/api/fields")
    assert r.status_code == 200
    assert r.json()[0]["name"] == "양자"
    assert r.json()[0]["subfields"][0]["name"] == "양자컴퓨팅"


def test_admin_requires_key(client):
    assert client.get("/api/admin/dashboard").status_code == 401
    assert client.get("/api/admin/dashboard",
                      headers={"X-Admin-Key": "wrong"}).status_code == 401


def test_admin_accepts_correct_key(client):
    r = client.get("/api/admin/dashboard", headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 200


def test_admin_can_create_subfield(client):
    r = client.post(
        "/api/admin/subfields",
        headers={"X-Admin-Key": settings.admin_key},
        json={"field_id": 1, "name": "양자센서", "query": "quantum sensing"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "양자센서"


def test_field_summary_includes_subfields_without_analysis(client):
    # 세부기술 하나 더 추가 — 분석이 아예 없는 상태로 남는다.
    r = client.post(
        "/api/admin/subfields",
        headers={"X-Admin-Key": settings.admin_key},
        json={"field_id": 1, "name": "양자센서", "query": "quantum sensing"},
    )
    assert r.status_code == 200
    quantum_sensing_id = r.json()["id"]

    # 첫 번째 세부기술(양자컴퓨팅, id=1)에는 done 상태의 분석을 하나 심어둔다.
    db = app.dependency_overrides[get_db]()
    db.add(Analysis(
        subfield_id=1, year=2024, status="done", query_hash="x",
        searched_count=120, analyzed_count=100,
    ))
    db.commit()
    db.close()

    r = client.get("/api/fields/1/summary?year=2024")
    assert r.status_code == 200
    body = r.json()
    assert body["field_name"] == "양자"
    assert body["year"] == 2024

    by_name = {s["subfield_name"]: s for s in body["subfields"]}
    assert by_name["양자컴퓨팅"]["status"] == "done"
    assert by_name["양자컴퓨팅"]["searched_count"] == 120
    assert by_name["양자컴퓨팅"]["analyzed_count"] == 100

    # 분석이 없는 세부기술도 목록에 포함되고 미실행으로 표시되어야 한다.
    assert by_name["양자센서"]["analysis_id"] is None
    assert by_name["양자센서"]["status"] == "미실행"
    assert by_name["양자센서"]["searched_count"] == 0
    assert by_name["양자센서"]["analyzed_count"] == 0
    assert quantum_sensing_id == by_name["양자센서"]["subfield_id"]

    assert body["total_searched"] == 120
    assert body["total_analyzed"] == 100


def _exhaust_budget(db):
    db.add(OpenAlexUsage(
        usage_date=datetime.now(timezone.utc).date(),
        cost_usd=settings.openalex_daily_budget_usd,
    ))
    db.commit()
    db.close()


def test_run_returns_429_when_budget_exhausted(client):
    db = app.dependency_overrides[get_db]()
    _exhaust_budget(db)

    r = client.post(
        "/api/admin/run",
        headers={"X-Admin-Key": settings.admin_key},
        json={"subfield_ids": [1], "year_from": 2023, "year_to": 2024},
    )
    assert r.status_code == 429
    assert "예산" in r.json()["detail"]


def test_preview_returns_429_when_budget_exhausted(client):
    # check_budget()이 OpenAlex 호출 전에 걸리므로 네트워크 monkeypatch 없이도 검증 가능.
    db = app.dependency_overrides[get_db]()
    _exhaust_budget(db)

    r = client.post(
        "/api/admin/preview",
        headers={"X-Admin-Key": settings.admin_key},
        json={"subfield_id": 1, "year_from": 2023, "year_to": 2024},
    )
    assert r.status_code == 429
    assert "예산" in r.json()["detail"]


def test_preview_includes_llm_cost_estimate(client, monkeypatch):
    """C3: /preview는 OpenAlex 비용만이 아니라 map 단계 LLM 비용까지 추정해
    합산 총액을 보여줘야 한다 — 관리자가 실제 지배적 지출을 보지 못한 채
    확정 버튼을 누르는 상황을 막기 위함."""
    async def fake_count_only(query, year_from, year_to, *, client):
        return 42, 0.001

    async def fake_oa_search(query, year_from, year_to, *, client, limit):
        return OpenAlexResult(papers=[], cost_usd=0.001, remaining="9", total_count=42)

    async def fake_kci_search(query, year_from, year_to, *, client, limit):
        return []

    monkeypatch.setattr(admin_module.openalex, "count_only", fake_count_only)
    monkeypatch.setattr(admin_module.openalex, "search", fake_oa_search)
    monkeypatch.setattr(admin_module.kci, "search", fake_kci_search)

    r = client.post(
        "/api/admin/preview",
        headers={"X-Admin-Key": settings.admin_key},
        json={"subfield_id": 1, "year_from": 2023, "year_to": 2024},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["estimated_papers_to_extract"] == 42
    assert body["estimated_llm_cost_usd"] > 0
    assert body["estimated_total_cost_usd"] == pytest.approx(
        body["estimated_cost_usd"] + body["estimated_llm_cost_usd"]
    )


def test_create_subfield_rejects_blank_query(client):
    r = client.post(
        "/api/admin/subfields",
        headers={"X-Admin-Key": settings.admin_key},
        json={"field_id": 1, "name": "빈검색어", "query": "   "},
    )
    assert r.status_code == 422


def test_create_subfield_rejects_blank_name(client):
    r = client.post(
        "/api/admin/subfields",
        headers={"X-Admin-Key": settings.admin_key},
        json={"field_id": 1, "name": "", "query": "quantum"},
    )
    assert r.status_code == 422


def test_run_rejects_inverted_year_range(client):
    r = client.post(
        "/api/admin/run",
        headers={"X-Admin-Key": settings.admin_key},
        json={"subfield_ids": [1], "year_from": 2024, "year_to": 2020},
    )
    assert r.status_code == 422


def test_run_rejects_out_of_range_year(client):
    r = client.post(
        "/api/admin/run",
        headers={"X-Admin-Key": settings.admin_key},
        json={"subfield_ids": [1], "year_from": 1800, "year_to": 2024},
    )
    assert r.status_code == 422


def test_run_rejects_empty_subfield_ids(client):
    r = client.post(
        "/api/admin/run",
        headers={"X-Admin-Key": settings.admin_key},
        json={"subfield_ids": [], "year_from": 2023, "year_to": 2024},
    )
    assert r.status_code == 422


def test_delete_subfield_blocked_when_analysis_history_exists(client):
    db = app.dependency_overrides[get_db]()
    db.add(Analysis(subfield_id=1, year=2024, status="done", query_hash="x"))
    db.commit()
    db.close()

    r = client.delete("/api/admin/subfields/1", headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 409
    assert "분석 이력" in r.json()["detail"]

    # 여전히 존재해야 한다.
    db = app.dependency_overrides[get_db]()
    assert db.get(Subfield, 1) is not None
    db.close()


def test_delete_subfield_succeeds_without_analysis_history(client):
    r = client.delete("/api/admin/subfields/1", headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 200

    db = app.dependency_overrides[get_db]()
    assert db.get(Subfield, 1) is None
    db.close()
