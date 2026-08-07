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
from app.models.analysis import Analysis, AnalysisPaper
from app.models.budget import OpenAlexUsage
from app.models.field import CountryComparison, Field, FieldReport, Subfield
from app.models.paper import Paper, PaperExtraction
from app.models.schedule import AnalysisRun
from app.routers import admin as admin_module
from app.services import search


@pytest.fixture
def client():
    # StaticPool: FastAPI가 sync 엔드포인트를 워커 스레드풀에서 실행하므로, 스레드마다
    # 새 커넥션을 여는 기본 풀로는 :memory: sqlite가 매번 빈 DB로 보인다.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
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


def test_admin_rejects_non_ascii_key_without_crashing(client):
    """비ASCII 키가 500이 아니라 401이어야 한다.

    secrets.compare_digest는 str을 받으면 ASCII만 허용해 한 글자만 벗어나도
    TypeError를 낸다. deps.require_admin이 양쪽을 bytes로 인코딩하는 이유가 이것이다.

    헤더 값을 bytes로 넣는 것이 이 테스트의 핵심이다 — HTTP 헤더는 와이어에서 바이트이고
    Starlette이 latin-1로 디코드해 넘긴다. str로 넣으면 httpx가 보내기 전에 ASCII로
    인코딩하려다 실패해서, 정작 서버 쪽 경로를 한 번도 밟지 못한다(한글은 latin-1로도
    인코딩되지 않아 애초에 전송 불가 — 실제로 도달할 수 있는 건 이런 latin-1 바이트다).
    """
    r = client.get("/api/admin/dashboard", headers={"X-Admin-Key": "kü".encode("latin-1")})
    assert r.status_code == 401


def test_admin_accepts_correct_key(client):
    r = client.get("/api/admin/dashboard", headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 200


def test_dashboard_stale_flag_accounts_for_country(client):
    """비KR 분석의 '갱신 필요' 판정.

    query_hash에 country를 넘기지 않으면 US 분석의 해시가 KR 해시와 비교돼 영원히
    stale로 뜨고, 관리자가 비용을 들여 무의미한 재실행을 누르게 된다.
    """
    db = app.dependency_overrides[get_db]()
    subfield = db.get(Subfield, 1)
    db.add(Analysis(subfield_id=1, year=2026, country="US", status="done",
                    query_hash=search.query_hash(subfield, 2026, 2026, "US"),
                    report_md="x", stats_json={}))
    db.commit()
    db.close()

    got = client.get("/api/admin/dashboard", headers={"X-Admin-Key": settings.admin_key}).json()
    cells = [c for r in got["rows"] for c in r["years"] if c["country"] == "US"]
    assert cells and all(c["stale"] is False for c in cells)


def test_dashboard_reports_active(client):
    """비활성 세부기술도 행은 나오되 active=False를 실어야 프론트가 선택 후보에서 뺄 수 있다."""
    db = app.dependency_overrides[get_db]()
    db.add(Subfield(field_id=1, name="비활성기술", query="x", active=False))
    db.commit()
    db.close()

    got = client.get("/api/admin/dashboard", headers={"X-Admin-Key": settings.admin_key}).json()
    by_name = {r["subfield_name"]: r["active"] for r in got["rows"]}
    assert by_name == {"양자컴퓨팅": True, "비활성기술": False}


def test_schedule_requires_admin_key(client):
    assert client.get("/api/admin/schedule").status_code == 401
    assert client.put(
        "/api/admin/schedule", json={"enabled": True, "day": 10, "hour": 3, "years_back": 1}
    ).status_code == 401
    assert client.post("/api/admin/schedule/run-now").status_code == 401


def test_schedule_get_returns_env_defaults_and_empty_history(client):
    r = client.get("/api/admin/schedule", headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] == settings.schedule_enabled
    assert body["day"] == settings.schedule_day
    assert body["hour"] == settings.schedule_hour
    assert body["years_back"] == settings.schedule_years_back
    assert body["timezone"] == settings.schedule_timezone
    assert body["next_run_at"]  # ISO 문자열
    assert body["history"] == []  # 아직 자동/수동 실행된 적 없음


@pytest.mark.parametrize("bad_payload", [
    {"enabled": True, "day": 0, "hour": 3, "years_back": 1},
    {"enabled": True, "day": 31, "hour": 3, "years_back": 1},
    {"enabled": True, "day": 10, "hour": 24, "years_back": 1},
    {"enabled": True, "day": 10, "hour": 3, "years_back": 6},
])
def test_schedule_put_rejects_out_of_range_values(client, bad_payload):
    r = client.put(
        "/api/admin/schedule", json=bad_payload, headers={"X-Admin-Key": settings.admin_key}
    )
    assert r.status_code == 422


def test_schedule_put_persists_new_settings(client):
    r = client.put(
        "/api/admin/schedule",
        json={"enabled": False, "day": 15, "hour": 4, "years_back": 2},
        headers={"X-Admin-Key": settings.admin_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["day"] == 15
    assert body["hour"] == 4
    assert body["years_back"] == 2

    again = client.get("/api/admin/schedule", headers={"X-Admin-Key": settings.admin_key}).json()
    assert again["day"] == 15
    assert again["enabled"] is False


def test_schedule_run_now_queues_and_appears_in_history(client):
    r = client.post("/api/admin/schedule/run-now", headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 200
    # 활성 세부기술 1개 × years_back 기본값 1(당해+직전연도) = 2건
    assert r.json()["queued_count"] == 2

    hist = client.get("/api/admin/schedule", headers={"X-Admin-Key": settings.admin_key}).json()["history"]
    assert len(hist) == 1
    assert hist[0]["trigger"] == "manual"
    assert hist[0]["queued_count"] == 2


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

    async def fake_oa_search(query, year_from, year_to, *, client, limit, country="KR"):
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


def test_preview_uses_the_requested_country_and_skips_kci_for_non_kr(client, monkeypatch):
    """국가를 빼면 미리보기가 늘 KR 기준으로 나와 다른 국가를 실행하려는 사람에게
    틀린 견적을 준다. KCI는 한국학술지 전용이라 타국에서는 부르지 않는다
    (search.collect와 같은 규약 — 표본에 국내지가 섞이면 실제 실행과 어긋난다)."""
    seen = {}

    async def fake_oa_search(query, year_from, year_to, *, client, limit, country="KR"):
        seen["country"] = country
        return OpenAlexResult(papers=[], cost_usd=0.001, remaining="9", total_count=7)

    async def fake_kci_search(query, year_from, year_to, *, client, limit):
        seen["kci_called"] = True
        return []

    monkeypatch.setattr(admin_module.openalex, "search", fake_oa_search)
    monkeypatch.setattr(admin_module.kci, "search", fake_kci_search)

    r = client.post(
        "/api/admin/preview",
        headers={"X-Admin-Key": settings.admin_key},
        json={"subfield_id": 1, "year_from": 2025, "year_to": 2025, "country": "US"},
    )
    assert r.status_code == 200
    assert seen["country"] == "US"
    assert "kci_called" not in seen
    assert r.json()["kci_sample_count"] == 0


def test_preview_defaults_to_korea(client, monkeypatch):
    """country를 안 보내던 기존 호출은 그대로 KR로 동작해야 한다."""
    seen = {}

    async def fake_oa_search(query, year_from, year_to, *, client, limit, country="KR"):
        seen["country"] = country
        return OpenAlexResult(papers=[], cost_usd=0.001, remaining="9", total_count=1)

    async def fake_kci_search(query, year_from, year_to, *, client, limit):
        seen["kci_called"] = True
        return []

    monkeypatch.setattr(admin_module.openalex, "search", fake_oa_search)
    monkeypatch.setattr(admin_module.kci, "search", fake_kci_search)

    client.post(
        "/api/admin/preview",
        headers={"X-Admin-Key": settings.admin_key},
        json={"subfield_id": 1, "year_from": 2025, "year_to": 2025},
    )
    assert seen["country"] == "KR"
    assert seen["kci_called"] is True


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


def test_queue_rejects_an_out_of_range_year(client):
    """연도는 본문 하나에 하나뿐이라 항목별 skipped로 못 옮긴다 — 스키마에서 막는다."""
    r = client.post(
        "/api/admin/queue",
        headers={"X-Admin-Key": settings.admin_key},
        json={"year": 1800, "analyses": [{"subfield_id": 1}]},
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


# ── 개별 분석(보고서) 삭제 (DELETE /api/admin/analyses/{id}) ──

def test_delete_analysis_requires_admin_key(client):
    db = app.dependency_overrides[get_db]()
    db.add(Analysis(subfield_id=1, year=2024, status="done", query_hash="x"))
    db.commit()
    db.close()

    assert client.delete("/api/admin/analyses/1").status_code == 401


def test_delete_analysis_404_when_missing(client):
    r = client.delete("/api/admin/analyses/999", headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 404


@pytest.mark.parametrize("status", ["pending", "searching", "extracting", "reducing"])
def test_delete_analysis_blocked_while_active(client, status):
    db = app.dependency_overrides[get_db]()
    db.add(Analysis(subfield_id=1, year=2024, status=status, query_hash="x"))
    db.commit()
    db.close()

    r = client.delete("/api/admin/analyses/1", headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 409
    assert "진행 중" in r.json()["detail"]

    db = app.dependency_overrides[get_db]()
    assert db.get(Analysis, 1) is not None
    db.close()


def test_delete_analysis_keeps_papers_and_extractions_deletes_links_and_runs(client):
    db = app.dependency_overrides[get_db]()
    a = Analysis(subfield_id=1, year=2024, status="done", query_hash="x",
                 report_md="report", analyzed_count=1, searched_count=1)
    db.add(a)
    db.flush()
    p = Paper(paper_key="k1", title="t", source="openalex")
    db.add(p)
    db.flush()
    db.add(AnalysisPaper(analysis_id=a.id, paper_id=p.id))
    db.add(PaperExtraction(paper_key="k1", subfield_id=1, model_ver="m1"))
    db.add(AnalysisRun(analysis_id=a.id, ran_at=datetime.now(timezone.utc),
                        searched_count=1, analyzed_count=1, new_papers=1, trigger="manual"))
    db.commit()
    analysis_id = a.id
    paper_id = p.id
    db.close()

    r = client.delete(f"/api/admin/analyses/{analysis_id}", headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 200

    db = app.dependency_overrides[get_db]()
    assert db.get(Analysis, analysis_id) is None
    assert db.query(AnalysisPaper).filter(AnalysisPaper.analysis_id == analysis_id).count() == 0
    assert db.query(AnalysisRun).filter(AnalysisRun.analysis_id == analysis_id).count() == 0
    # papers / paper_extractions는 다른 세부기술·연도와 공유하는 캐시라 반드시 남아 있어야 한다.
    assert db.get(Paper, paper_id) is not None
    assert db.query(PaperExtraction).filter(PaperExtraction.paper_key == "k1").count() == 1
    db.close()


def test_delete_analysis_then_subfield_delete_succeeds(client):
    db = app.dependency_overrides[get_db]()
    db.add(Analysis(subfield_id=1, year=2024, status="done", query_hash="x"))
    db.commit()
    db.close()

    # 분석 이력이 있는 동안은 세부기술 삭제가 막힌다.
    blocked = client.delete("/api/admin/subfields/1", headers={"X-Admin-Key": settings.admin_key})
    assert blocked.status_code == 409

    ok = client.delete("/api/admin/analyses/1", headers={"X-Admin-Key": settings.admin_key})
    assert ok.status_code == 200

    # 분석을 지운 뒤에는 세부기술 삭제가 풀린다.
    r = client.delete("/api/admin/subfields/1", headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 200


# ── report_md 각주 치환 (public.py::_apply_footnotes, GET /api/analyses/{id}) ──

def _done_analysis_with_papers(db, report_md, papers):
    a = Analysis(
        subfield_id=1, year=2025, status="done", query_hash="h", report_md=report_md,
        searched_count=len(papers), analyzed_count=len(papers),
    )
    db.add(a)
    db.flush()
    for p in papers:
        db.add(p)
    db.flush()
    for p in papers:
        db.add(AnalysisPaper(analysis_id=a.id, paper_id=p.id))
    db.commit()
    db.refresh(a)
    return a


def test_analysis_report_footnotes_parenthesized_title(client):
    db = app.dependency_overrides[get_db]()
    paper = Paper(
        paper_key="k1", title="Improving Zero-Noise Extrapolation for Quantum Circuits",
        journal="Nature", year=2025, doi="10.1234/xyz", source="openalex",
    )
    md = "오류 완화 기법을 제안했다 (Improving Zero-Noise Extrapolation for Quantum Circuits)."
    title = paper.title
    a = _done_analysis_with_papers(db, md, [paper])
    db.close()

    r = client.get(f"/api/analyses/{a.id}")
    assert r.status_code == 200
    body = r.json()
    assert "[\\[1\\]](#ref-1)" in body["report_md"]
    assert "Improving Zero-Noise Extrapolation" not in body["report_md"]
    assert body["references"] == [
        {"n": 1, "title": title, "journal": "Nature", "year": 2025, "doi": "10.1234/xyz"}
    ]


def test_analysis_report_leaves_unmatched_parenthetical_untouched(client):
    db = app.dependency_overrides[get_db]()
    paper = Paper(paper_key="k2", title="Some Unrelated Paper Title", source="openalex")
    md = "본문 중 (전혀 다른 텍스트)가 있다."
    a = _done_analysis_with_papers(db, md, [paper])
    db.close()

    r = client.get(f"/api/analyses/{a.id}")
    body = r.json()
    assert body["report_md"] == md
    assert body["references"] == []


def test_analysis_report_reuses_footnote_number_for_repeated_citation(client):
    db = app.dependency_overrides[get_db]()
    paper = Paper(paper_key="k3", title="Repeated Paper Title", journal="J", year=2024, source="openalex")
    md = "처음 언급 (Repeated Paper Title). 다시 언급 (Repeated Paper Title)."
    a = _done_analysis_with_papers(db, md, [paper])
    db.close()

    r = client.get(f"/api/analyses/{a.id}")
    body = r.json()
    assert body["report_md"].count("[\\[1\\]](#ref-1)") == 2
    assert len(body["references"]) == 1
    assert body["references"][0]["n"] == 1


def test_analysis_report_footnotes_year_prefixed_citation(client):
    """분석 7 재현: LLM이 괄호 안에 '[2025] 제목' 형태로 연도 접두사를 붙인 경우도
    치환돼야 한다 — 완전 일치만 보던 이전 구현은 이 형태를 전혀 못 잡았다."""
    db = app.dependency_overrides[get_db]()
    paper = Paper(
        paper_key="k4",
        title="Development of High Density 3D NAND Flash Memory",
        journal="IEEE", year=2025, source="openalex",
    )
    md = "밀도를 크게 높였다 ([2025] Development of High Density 3D NAND Flash Memory)."
    title = paper.title
    a = _done_analysis_with_papers(db, md, [paper])
    db.close()

    r = client.get(f"/api/analyses/{a.id}")
    body = r.json()
    assert "[\\[1\\]](#ref-1)" in body["report_md"]
    assert "Development of High Density" not in body["report_md"]
    assert len(body["references"]) == 1
    assert body["references"][0]["title"] == title


def test_analysis_report_footnotes_short_title_not_partial_matched(client):
    """제목이 짧으면(15자 미만) 괄호 안 다른 텍스트의 일부로 우연히 매칭되지 않아야 한다."""
    db = app.dependency_overrides[get_db]()
    paper = Paper(paper_key="k5", title="AI Diagnosis", source="openalex")  # 12자, 부분매칭 대상 아님
    md = "제안된 방법이다 (AI Diagnosis based on deep learning approach)."
    a = _done_analysis_with_papers(db, md, [paper])
    db.close()

    r = client.get(f"/api/analyses/{a.id}")
    body = r.json()
    assert body["report_md"] == md
    assert body["references"] == []


def test_analysis_report_footnotes_prefers_longer_title_when_substring(client):
    """한 제목이 다른 제목의 부분 문자열인 경우, 실제로 인용된 긴 제목의 논문으로
    귀속돼야 한다 — 짧은 제목을 먼저 보면 잘못된 논문이 걸릴 수 있다."""
    db = app.dependency_overrides[get_db]()
    short_paper = Paper(paper_key="k6", title="Graphene Growth Method", source="openalex")
    long_paper = Paper(
        paper_key="k7",
        title="Advanced Graphene Growth Method for Flexible Devices",
        journal="Nature", year=2023, source="openalex",
    )
    md = "새로운 성장법을 제시했다 (Advanced Graphene Growth Method for Flexible Devices)."
    long_title = long_paper.title
    a = _done_analysis_with_papers(db, md, [short_paper, long_paper])
    db.close()

    r = client.get(f"/api/analyses/{a.id}")
    body = r.json()
    assert len(body["references"]) == 1
    assert body["references"][0]["title"] == long_title


def test_analysis_report_footnotes_matches_when_llm_drops_spaces_around_numbers(client):
    """분석 8 재현: OpenAlex 원문이 태그 앞뒤에 공백을 넣어("Hf <sub>0.5</sub> Zr")
    strip_html 후 papers.title에 "Hf 0.5 Zr 0.5 O 2"로 저장되지만, Gemini는 인용할 때
    공백 없이 "Hf0.5Zr0.5O2"로 붙여 쓴다. 단어 단위로 이어 붙이던 이전 정규식 방식은
    토큰 수 자체가 달라 매칭에 실패했다 — 공백을 완전히 제거한 키 비교로만 잡힌다."""
    db = app.dependency_overrides[get_db]()
    paper = Paper(
        paper_key="k9",
        title="Low-Thermal-Budget Ferroelectricity of Hf 0.5 Zr 0.5 O 2 Thin Films",
        journal="APL", year=2025, source="openalex",
    )
    md = "저온 공정으로 강유전성을 구현했다 (Low-Thermal-Budget Ferroelectricity of Hf0.5Zr0.5O2 Thin Films)."
    title = paper.title
    a = _done_analysis_with_papers(db, md, [paper])
    db.close()

    r = client.get(f"/api/analyses/{a.id}")
    body = r.json()
    assert "[\\[1\\]](#ref-1)" in body["report_md"]
    assert len(body["references"]) == 1
    assert body["references"][0]["title"] == title


def test_analysis_report_footnotes_matches_when_citation_has_leftover_html_tags(client):
    """분석 8 재현: 태그 제거 마이그레이션 이전에 생성된 report_md는 Gemini가 원문 그대로
    받은 HTML 태그(<sub>, <i> 등)를 포함해 제목을 인용한다. DB의 papers.title은 깨끗하므로
    양쪽에 strip_html을 적용한 키로 비교해야 매칭된다."""
    db = app.dependency_overrides[get_db]()
    paper = Paper(
        paper_key="k10",
        title="Enhanced Ferroelectricity in Hf 1− X Zr X O 2 Thin Films",
        journal="ACS", year=2026, source="openalex",
    )
    md = (
        "특성이 개선되었다 (Enhanced Ferroelectricity in Hf <sub> 1− <i>X</i> </sub> "
        "Zr <i> <sub>X</sub> </i> O <sub>2</sub> Thin Films)."
    )
    title = paper.title
    a = _done_analysis_with_papers(db, md, [paper])
    db.close()

    r = client.get(f"/api/analyses/{a.id}")
    body = r.json()
    assert "[\\[1\\]](#ref-1)" in body["report_md"]
    assert len(body["references"]) == 1
    assert body["references"][0]["title"] == title


def test_analysis_report_footnotes_exact_match_still_works(client):
    """기존에 동작하던 '괄호 안이 제목과 정확히 일치' 케이스가 회귀 없이 계속 동작해야 한다."""
    db = app.dependency_overrides[get_db]()
    paper = Paper(paper_key="k8", title="Exact Match Paper Title Example", source="openalex")
    md = "성과를 냈다 (Exact Match Paper Title Example)."
    a = _done_analysis_with_papers(db, md, [paper])
    db.close()

    r = client.get(f"/api/analyses/{a.id}")
    body = r.json()
    assert "[\\[1\\]](#ref-1)" in body["report_md"]
    assert len(body["references"]) == 1


def test_analysis_report_footnotes_matches_title_with_nested_parens(client):
    """분석 7 재현: 논문 제목 자체가 괄호를 포함하면(예: "TrioN (3N0C)") 안쪽 괄호만
    잡던 이전 정규식은 바깥 인용 전체를 치환하지 못했다 — 한 단계 중첩까지 잡아야 한다."""
    db = app.dependency_overrides[get_db]()
    paper = Paper(
        paper_key="k11",
        title="Highly-efficient and scalable TrioN (3N0C) synaptic cell for analog process-in-memory",
        journal="Nature Electronics", year=2025, source="openalex",
    )
    md = (
        "새로운 소자 구조를 제안했다 (Highly-efficient and scalable TrioN (3N0C) "
        "synaptic cell for analog process-in-memory)."
    )
    title = paper.title
    a = _done_analysis_with_papers(db, md, [paper])
    db.close()

    r = client.get(f"/api/analyses/{a.id}")
    body = r.json()
    assert "[\\[1\\]](#ref-1)" in body["report_md"]
    assert "TrioN" not in body["report_md"]
    assert len(body["references"]) == 1
    assert body["references"][0]["title"] == title


def test_analysis_report_footnotes_leaves_enumeration_parens_untouched(client):
    """중첩 괄호를 허용해도, 논문 인용이 아닌 일반 나열 괄호(예: 소재 종류 나열)는
    치환되면 안 된다 — 매칭은 여전히 제목 키가 실제로 포함될 때만 성립해야 한다."""
    db = app.dependency_overrides[get_db]()
    paper = Paper(
        paper_key="k12",
        title="Highly-efficient and scalable TrioN (3N0C) synaptic cell for analog process-in-memory",
        journal="Nature Electronics", year=2025, source="openalex",
    )
    md = "다양한 소재가 검토되었다 (산화물 반도체, 2차원 소재, 강유전체)."
    a = _done_analysis_with_papers(db, md, [paper])
    db.close()

    r = client.get(f"/api/analyses/{a.id}")
    body = r.json()
    assert body["report_md"] == md
    assert body["references"] == []


def _seed_done_analysis(client, subfield_name, report_md):
    """분야 1에 세부기술 하나를 추가하고 done 상태 분석을 붙인다."""
    db = app.dependency_overrides[get_db]()
    s = Subfield(field_id=1, name=subfield_name, query="q")
    db.add(s)
    db.flush()
    db.add(Analysis(
        subfield_id=s.id, year=2026, status="done", query_hash="h",
        report_md=report_md, stats_json={},
    ))
    db.commit()
    db.close()


def _drain_report_queue():
    """pending 분야 보고서·로드맵 점검을 전부 처리한다 — runner.loop 없이 테스트에서
    직접 소화한다. POST는 이제 pending 큐잉만 하므로, done을 기대하는 테스트는 이걸
    호출한 뒤 조회한다. advance_field_reports가 한 틱에 하나씩 처리하므로 반복한다."""
    import asyncio

    from app.models.field import FieldReport, RoadmapCheck
    from app.services import runner

    for _ in range(100):  # 무한 루프 방지 상한
        db = app.dependency_overrides[get_db]()
        pending = (
            db.query(FieldReport).filter(FieldReport.status == "pending").count()
            + db.query(RoadmapCheck).filter(RoadmapCheck.status == "pending").count()
        )
        if not pending:
            db.close()
            return
        asyncio.run(runner.advance_field_reports(db))
        db.close()


def test_field_report_roundtrip(client, monkeypatch):
    """rollup 호출부: 완성된 세부기술 보고서만 합성 입력으로 들어가고,
    결과가 캐시돼 공개 조회로 읽힌다."""
    _seed_done_analysis(client, "초전도 큐비트", "## 성과\n큐비트 성과 본문")
    # 보고서 없이 done인 행은 입력에서 빠져야 한다 — 넣으면 모델이 지어낸다.
    _seed_done_analysis(client, "빈 세부기술", None)

    captured = {}

    async def fake_generate(system, user, *, thinking, **kwargs):
        captured["user"] = user
        return "# 양자 2026년 분야 보고서"

    monkeypatch.setattr("app.clients.gemini_sync.generate", fake_generate)

    # POST는 pending 큐잉만 한다(즉시 실행 아님).
    r = client.post("/api/admin/fields/1/report?year=2026",
                    headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    assert client.get("/api/fields/1/report?year=2026").json()["status"] == "pending"

    _drain_report_queue()  # runner가 처리

    assert "큐비트 성과 본문" in captured["user"]
    assert "빈 세부기술" not in captured["user"]
    got = client.get("/api/fields/1/report?year=2026").json()
    assert got["status"] == "done"
    assert got["report_md"] == "# 양자 2026년 분야 보고서"
    assert got["source_count"] == 1
    assert got["stale"] is False


def test_field_report_marks_stale_when_new_subfield_completes(client, monkeypatch):
    _seed_done_analysis(client, "초전도 큐비트", "본문 A")

    async def fake_generate(system, user, *, thinking, **kwargs):
        return "합성 결과"

    monkeypatch.setattr("app.clients.gemini_sync.generate", fake_generate)
    client.post("/api/admin/fields/1/report?year=2026",
                headers={"X-Admin-Key": settings.admin_key})
    _drain_report_queue()

    _seed_done_analysis(client, "이온트랩", "본문 B")
    assert client.get("/api/fields/1/report?year=2026").json()["stale"] is True


def test_field_report_refuses_when_no_subfield_report(client):
    """빈 입력으로 LLM을 부르면 분야 성과를 통째로 지어낸다 — 큐잉 시점(enqueue)에
    409로 막는다."""
    r = client.post("/api/admin/fields/1/report?year=2026",
                    headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 409


def test_field_report_404_for_unknown_field(client):
    """없는 분야는 404 — "보고서가 없다"(409)와 구분되어야 한다."""
    r = client.post("/api/admin/fields/999/report?year=2026",
                    headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 404


def test_field_report_404_before_generation(client):
    assert client.get("/api/fields/1/report?year=2026").status_code == 404


# ── 로드맵 이행 점검 ──

_ROADMAP_MD = """# 테스트 로드맵

## 1. 중점기술 A

| 단계 | 시기 | 기술적 목표 |
|------|------|------------|
| 1단계 | ~'26년 | 목표 하나 |
| 2단계 | ~'30년 | 목표 둘 |

## 2. 중점기술 B

| 구분 | 기술적 목표 |
|------|------------|
| 통합 | 목표 셋 |
"""


def _put_roadmap(client, content=_ROADMAP_MD, version="v1"):
    return client.put("/api/admin/fields/1/roadmap",
                      json={"version_label": version, "content_md": content},
                      headers={"X-Admin-Key": settings.admin_key})


def test_count_goal_rows_skips_headers_and_separators():
    """헤더 행과 구분선을 빼고 본문 행만 센다 — 이 숫자가 프롬프트에 주입돼
    전수 점검을 강제하므로, 틀리면 점검이 조용히 축소된다."""
    from app.services import reducer
    assert reducer.count_goal_rows(_ROADMAP_MD) == 3
    assert reducer.count_goal_rows("표가 전혀 없는 본문") == 0


def test_roadmap_put_rejects_content_without_table(client):
    """표가 없으면 goal_count가 0이 되어 전수 점검 강제가 무력화된다 — 저장 시점에 막는다."""
    r = _put_roadmap(client, content="단계별 목표를 표 없이 줄글로만 썼습니다.")
    assert r.status_code == 422


def test_roadmap_put_and_get(client):
    assert _put_roadmap(client).json()["goal_count"] == 3
    got = client.get("/api/admin/fields/1/roadmap",
                     headers={"X-Admin-Key": settings.admin_key}).json()
    assert got["version_label"] == "v1"
    assert got["goal_count"] == 3


def test_roadmap_get_returns_blank_when_absent(client):
    """미등록 분야는 404가 아니라 빈 값 — 편집 화면이 그대로 새 입력 폼이 된다."""
    got = client.get("/api/admin/fields/1/roadmap",
                     headers={"X-Admin-Key": settings.admin_key}).json()
    assert got["content_md"] == ""
    assert got["goal_count"] == 0


def test_roadmap_check_refuses_without_roadmap(client):
    _seed_done_analysis(client, "세부기술 A", "본문 A")
    r = client.post("/api/admin/fields/1/roadmap-check?year=2026",
                    headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 409
    assert "로드맵" in r.json()["detail"]


def test_roadmap_check_refuses_without_subfield_report(client):
    _put_roadmap(client)
    r = client.post("/api/admin/fields/1/roadmap-check?year=2026",
                    headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 409


def test_roadmap_check_injects_goal_count_and_verifies_row_count(client, monkeypatch):
    """목표 행 수가 프롬프트에 주입되고, 생성된 보고서의 행 수가 그와 일치하면
    incomplete=False로 저장된다."""
    _seed_done_analysis(client, "세부기술 A", "본문 A")
    _put_roadmap(client)

    captured = {}
    # 목표 3행과 같은 3행짜리 점검 표를 돌려준다.
    good = ("| 단계 | 목표 | 판정 | 근거 |\n|---|---|---|---|\n"
            "| 1단계 | 목표 하나 | 관련 연구 확인 | 본문 A |\n"
            "| 2단계 | 목표 둘 | 데이터 없음 | - |\n"
            "| 통합 | 목표 셋 | 데이터 없음 | - |\n")

    async def fake_generate(system, user, *, thinking, **kwargs):
        captured["system"] = system
        captured["user"] = user
        return good

    monkeypatch.setattr("app.clients.gemini_sync.generate", fake_generate)
    r = client.post("/api/admin/fields/1/roadmap-check?year=2026",
                    headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    _drain_report_queue()

    # 행 수가 프롬프트에 실제로 박혔는지 — 이게 빠지면 모델이 목표를 뭉뚱그린다.
    assert "총 3개" in captured["system"]
    # 로드맵(A)과 보고서(B)가 둘 다 입력에 들어갔다.
    assert "목표 하나" in captured["user"] and "본문 A" in captured["user"]

    got = client.get("/api/fields/1/roadmap-check?year=2026").json()
    assert got["status"] == "done"
    assert got["goal_count"] == 3 and got["checked_count"] == 3
    assert got["incomplete"] is False
    assert got["stale"] is False
    assert got["roadmap_version"] == "v1"


def test_roadmap_check_flags_incomplete_when_rows_are_dropped(client, monkeypatch):
    """모델이 목표를 뭉뚱그려 행이 줄면(실측된 실패 모드) incomplete=True로 남는다."""
    _seed_done_analysis(client, "세부기술 A", "본문 A")
    _put_roadmap(client)

    short = ("| 단계 | 목표 | 판정 | 근거 |\n|---|---|---|---|\n"
             "| 1~2단계 | 목표 하나~둘 | 관련 연구 확인 | 본문 A |\n")

    async def fake_generate(system, user, *, thinking, **kwargs):
        return short

    monkeypatch.setattr("app.clients.gemini_sync.generate", fake_generate)
    client.post("/api/admin/fields/1/roadmap-check?year=2026",
                headers={"X-Admin-Key": settings.admin_key})
    _drain_report_queue()

    got = client.get("/api/fields/1/roadmap-check?year=2026").json()
    assert got["goal_count"] == 3 and got["checked_count"] == 1
    assert got["incomplete"] is True


def test_roadmap_check_goes_stale_when_roadmap_version_changes(client, monkeypatch):
    """로드맵만 개정돼도 재생성 대상이다 — 세부기술 보고서 수는 그대로일 수 있다."""
    _seed_done_analysis(client, "세부기술 A", "본문 A")
    _put_roadmap(client, version="v1")

    async def fake_generate(system, user, *, thinking, **kwargs):
        return "| 단계 | 목표 | 판정 | 근거 |\n|---|---|---|---|\n| 1 | a | 데이터 없음 | - |\n"

    monkeypatch.setattr("app.clients.gemini_sync.generate", fake_generate)
    client.post("/api/admin/fields/1/roadmap-check?year=2026",
                headers={"X-Admin-Key": settings.admin_key})
    _drain_report_queue()
    assert client.get("/api/fields/1/roadmap-check?year=2026").json()["stale"] is False

    _put_roadmap(client, version="v2")
    assert client.get("/api/fields/1/roadmap-check?year=2026").json()["stale"] is True


def test_roadmap_delete_keeps_existing_check(client, monkeypatch):
    """로드맵을 지워도 그 판본으로 만든 점검 보고서는 남는다."""
    _seed_done_analysis(client, "세부기술 A", "본문 A")
    _put_roadmap(client)

    async def fake_generate(system, user, *, thinking, **kwargs):
        return "| 단계 | 목표 | 판정 | 근거 |\n|---|---|---|---|\n| 1 | a | 데이터 없음 | - |\n"

    monkeypatch.setattr("app.clients.gemini_sync.generate", fake_generate)
    client.post("/api/admin/fields/1/roadmap-check?year=2026",
                headers={"X-Admin-Key": settings.admin_key})
    _drain_report_queue()
    client.delete("/api/admin/fields/1/roadmap", headers={"X-Admin-Key": settings.admin_key})
    assert client.get("/api/fields/1/roadmap-check?year=2026").status_code == 200


def test_roadmap_check_requires_admin_key(client):
    assert client.post("/api/admin/fields/1/roadmap-check?year=2026").status_code == 401
    assert client.get("/api/admin/fields/1/roadmap").status_code == 401


def test_roadmap_instruction_has_only_goal_count_placeholder():
    """ROADMAP_CHECK_INSTRUCTION은 .format(goal_count=...)으로 렌더된다 — 본문에
    다른 중괄호를 넣으면(예시 제목에 {연도}를 쓰는 등) 렌더가 통째로 터진다.
    실제로 한 번 깨뜨렸던 지점이라 자리표시자 집합을 고정한다."""
    import string
    from app.prompts import ROADMAP_CHECK_INSTRUCTION

    fields = {f for _, f, _, _ in string.Formatter().parse(ROADMAP_CHECK_INSTRUCTION) if f is not None}
    assert fields == {"goal_count"}
    # 렌더 자체가 되는지도 확인한다.
    assert "총 65개" in ROADMAP_CHECK_INSTRUCTION.format(goal_count=65)


def test_fields_reports_current_year_progress(client):
    """랜딩 화면 진행 파이용 — 당해연도 done 건수. 비활성 세부기술은 분모(활성 수)에서
    빠지므로 분자에서도 빠져야 한다. 그러지 않으면 파이가 100%를 넘는다."""
    from datetime import datetime, timezone

    year = datetime.now(timezone.utc).year
    db = app.dependency_overrides[get_db]()
    active = Subfield(field_id=1, name="활성 세부기술", query="q", active=True)
    inactive = Subfield(field_id=1, name="비활성 세부기술", query="q", active=False)
    db.add_all([active, inactive])
    db.flush()
    for s in (active, inactive):
        db.add(Analysis(subfield_id=s.id, year=year, status="done", query_hash="h",
                        report_md="본문", stats_json={}))
    db.commit()
    db.close()

    row = next(f for f in client.get("/api/fields").json() if f["id"] == 1)
    assert row["current_year"] == year
    # 활성 1건만 센다(비활성 1건 제외). 픽스처의 "양자컴퓨팅"은 분석이 없다.
    assert row["current_year_done"] == 1


def test_fields_current_year_done_does_not_double_count_other_countries(client):
    """국가 필터가 없으면 같은 세부기술의 KR·US 분석이 둘 다 세어져 분자(done)가
    분모(활성 세부기술 수)를 넘어설 수 있다(리뷰 지적)."""
    from datetime import datetime, timezone

    year = datetime.now(timezone.utc).year
    db = app.dependency_overrides[get_db]()
    for country in ("KR", "US"):
        db.add(Analysis(subfield_id=1, year=year, country=country, status="done",
                        query_hash="h", report_md="본문", stats_json={}))
    db.commit()
    db.close()

    row = next(f for f in client.get("/api/fields").json() if f["id"] == 1)
    # 활성 세부기술은 픽스처의 "양자컴퓨팅" 1개뿐이므로 분자도 1을 넘을 수 없다.
    assert row["current_year_done"] == 1


# ── 분야 보고서 큐잉·일괄 실행 ──

def test_field_report_failure_marks_failed_not_crash_loop(client, monkeypatch):
    """생성 중 예외가 나면 그 행만 failed로 남고, 루프(_process_report)가 흡수해
    다른 큐 항목을 계속 처리한다."""
    _seed_done_analysis(client, "세부기술 A", "본문 A")

    async def boom(system, user, *, thinking, **kwargs):
        raise RuntimeError("LLM 폭발")

    monkeypatch.setattr("app.clients.gemini_sync.generate", boom)
    client.post("/api/admin/fields/1/report?year=2026",
                headers={"X-Admin-Key": settings.admin_key})
    _drain_report_queue()

    got = client.get("/api/fields/1/report?year=2026").json()
    assert got["status"] == "failed"
    assert "LLM 폭발" in got["error"]


def test_field_reports_overview_lists_status(client, monkeypatch):
    """관리자 현황 표 — 분야별 종합/점검 상태를 한 번에 내려준다."""
    _seed_done_analysis(client, "세부기술 A", "본문 A")

    async def fake_generate(system, user, *, thinking, **kwargs):
        return "합성 결과"

    monkeypatch.setattr("app.clients.gemini_sync.generate", fake_generate)
    client.post("/api/admin/fields/1/report?year=2026",
                headers={"X-Admin-Key": settings.admin_key})
    _drain_report_queue()

    ov = client.get("/api/admin/field-reports?year=2026",
                    headers={"X-Admin-Key": settings.admin_key}).json()
    row = next(r for r in ov["rows"] if r["field_id"] == 1)
    assert row["report"]["status"] == "done"
    assert row["roadmap_check"] is None  # 점검은 안 돌렸다
    assert row["roadmap"] is None


def test_field_reports_carries_roadmap_version_and_goal_count(client):
    """어느 판본으로 점검했는지가 보고서 신뢰도를 좌우한다 — 목록에서 바로 보여야 한다.
    분야마다 /fields/{id}/roadmap을 따로 부르면 10번 나간다."""
    client.put(
        "/api/admin/fields/1/roadmap", headers={"X-Admin-Key": settings.admin_key},
        json={"version_label": "2026 제1호", "content_md":
              "| 단계 | 시기 | 목표 |\n|---|---|---|\n| 1 | 2026 | 가 |\n| 2 | 2027 | 나 |"},
    )

    rows = client.get("/api/admin/field-reports?year=2026",
                      headers={"X-Admin-Key": settings.admin_key}).json()["rows"]
    row = next(r for r in rows if r["field_id"] == 1)
    assert row["roadmap"] == {"version_label": "2026 제1호", "goal_count": 2}


def test_field_reports_gives_null_roadmap_when_unregistered(client):
    """미등록과 '판본을 못 읽었다'가 같아 보이면 안 된다."""
    rows = client.get("/api/admin/field-reports?year=2026",
                      headers={"X-Admin-Key": settings.admin_key}).json()["rows"]
    assert all(r["roadmap"] is None for r in rows)


def test_subfield_reports_endpoint_returns_bodies(client):
    """세부기술 첨부 토글용 — 완성된 세부기술 보고서 본문을 목록으로 내려준다."""
    _seed_done_analysis(client, "세부기술 A", "## 성과\nA 본문")
    _seed_done_analysis(client, "빈 것", None)  # 본문 없는 건 제외

    # 같은 세부기술에 다른 국가 분석이 있어도 부록은 늘어나지 않는다 — 다른 공개
    # 집계와 같이 KR만 싣는다(안 걸면 세부기술마다 국가 수만큼 행이 나온다).
    db = app.dependency_overrides[get_db]()
    subfield_id = db.query(Subfield).filter(Subfield.name == "세부기술 A").one().id
    db.add(Analysis(subfield_id=subfield_id, year=2026, country="US", status="done",
                    query_hash="h", report_md="## 성과\nUS 본문", stats_json={}))
    db.commit()
    db.close()

    got = client.get("/api/fields/1/subfield-reports?year=2026").json()
    assert len(got["reports"]) == 1
    assert got["reports"][0]["name"] == "세부기술 A"
    assert "A 본문" in got["reports"][0]["report_md"]
    assert "US 본문" not in got["reports"][0]["report_md"]


def test_subfield_reports_apply_footnotes(client):
    """부록도 세부기술 보고서 화면과 똑같이 각주 치환을 해야 한다 — 안 하면 논문 제목이
    full name 그대로 노출된다(사용자 신고). 괄호 인용이 [n] 각주로 바뀌고 references가
    함께 내려와야 한다."""
    db = app.dependency_overrides[get_db]()
    s = Subfield(field_id=1, name="세부기술 X", query="q")
    db.add(s)
    db.flush()
    a = Analysis(subfield_id=s.id, year=2026, status="done", query_hash="h",
                 report_md="성과를 달성했다 (Improving Zero-Noise Extrapolation).", stats_json={})
    db.add(a)
    db.flush()
    p = Paper(paper_key="pk", title="Improving Zero-Noise Extrapolation",
              journal="Nature", year=2025, doi="10.1/x", source="openalex")
    db.add(p)
    db.flush()
    db.add(AnalysisPaper(analysis_id=a.id, paper_id=p.id))
    db.commit()
    db.close()

    rep = client.get("/api/fields/1/subfield-reports?year=2026").json()["reports"][0]
    # 논문 제목 full name이 각주 링크로 치환됐다.
    assert "Improving Zero-Noise Extrapolation" not in rep["report_md"]
    assert "(#ref-1)" in rep["report_md"]
    assert rep["references"][0]["title"] == "Improving Zero-Noise Extrapolation"


def test_analysis_report_footnotes_backtick_cited_title(client):
    """LLM이 괄호 대신 백틱(코드 스팬)으로 논문을 인용하는 경우가 있다.

    실측(subfield 10 / 2026 안전·신뢰 AI): 서술부 인용 26건 중 23건이 백틱이었고
    괄호는 3건뿐이라, 괄호만 보던 매칭이 제목을 통째로 노출시켰다.
    """
    db = app.dependency_overrides[get_db]()
    paper = Paper(
        paper_key="k-bt", title="EG-RAG: Retrieval-Augmented Generation with Evidence Graph",
        journal="ACL", year=2026, doi="10.1234/egrag", source="openalex",
    )
    md = "예를 들어, `EG-RAG: Retrieval-Augmented Generation with Evidence Graph`은 노이즈를 제거했다."
    a = _done_analysis_with_papers(db, md, [paper])

    body = client.get(f"/api/analyses/{a.id}").json()
    assert paper.title not in body["report_md"]
    assert "[\\[1\\]](#ref-1)" in body["report_md"]
    assert [r["title"] for r in body["references"]] == [paper.title]


def test_analysis_report_backtick_non_title_is_left_alone(client):
    """백틱 안이라도 논문 제목이 아니면 건드리지 않는다 — 용어·코드 표기를 깨면 안 된다."""
    db = app.dependency_overrides[get_db]()
    paper = Paper(paper_key="k-x", title="Some Completely Unrelated Paper Title Here",
                  journal="J", year=2026, doi=None, source="openalex")
    md = "양자화는 `torch.quantization.prepare_qat` 함수로 적용한다."
    a = _done_analysis_with_papers(db, md, [paper])

    body = client.get(f"/api/analyses/{a.id}").json()
    assert "`torch.quantization.prepare_qat`" in body["report_md"]
    assert body["references"] == []


def test_analysis_report_collapses_footnote_only_bullets(client):
    """인용만 있는 불릿 목록은 한 줄로 접는다.

    실측(subfield 8 / 2026): LLM이 인용을 문단이 아니라 불릿으로 나열해
    치환 후 '[1]'만 있는 불릿이 5줄씩 쌓였다(서술부 불릿 30줄).
    """
    db = app.dependency_overrides[get_db]()
    papers = [
        Paper(paper_key="b1", title="Ultra-efficient Physical Field Computing Networks",
              journal="Nat Commun", year=2026, doi=None, source="openalex"),
        Paper(paper_key="b2", title="LogFlex: Flexible-Bit Log Arithmetic Accelerator",
              journal="IEEE Micro", year=2026, doi=None, source="openalex"),
    ]
    md = (
        "로그 양자화는 핵심 기법으로 자리 잡았습니다.\n\n"
        "*   (Ultra-efficient Physical Field Computing Networks)\n"
        "*   (LogFlex: Flexible-Bit Log Arithmetic Accelerator)\n\n"
        "다음 문단입니다.\n"
    )
    a = _done_analysis_with_papers(db, md, papers)

    out = client.get(f"/api/analyses/{a.id}").json()["report_md"]
    assert "*   [\\[1\\]]" not in out and "* [\\[1\\]]" not in out
    assert "[\\[1\\]](#ref-1)[\\[2\\]](#ref-2)" in out
    assert "다음 문단입니다." in out


def test_analysis_report_keeps_bullets_that_have_text(client):
    """본문이 있는 불릿은 그대로 둔다 — 접기는 인용만 있는 줄에만 적용한다."""
    db = app.dependency_overrides[get_db]()
    paper = Paper(paper_key="b3", title="A Meaningful Paper Title For Testing",
                  journal="J", year=2026, doi=None, source="openalex")
    md = "- 양자화 기법이 발전했다 (A Meaningful Paper Title For Testing)\n"
    a = _done_analysis_with_papers(db, md, [paper])

    out = client.get(f"/api/analyses/{a.id}").json()["report_md"]
    assert out.startswith("- 양자화 기법이 발전했다 ")


def test_analysis_exposes_sections_with_shared_footnotes(client):
    """세부 보고서도 각주 치환을 받고, 번호는 종합 보고서와 같은 체계를 쓴다 —
    펼쳤을 때 [n]이 다른 논문을 가리키면 읽는 사람이 혼란스럽다."""
    db = app.dependency_overrides[get_db]()
    papers = [
        Paper(paper_key="s1", title="Solid Electrolyte Interface Engineering Study",
              journal="J1", year=2026, doi=None, source="openalex"),
        Paper(paper_key="s2", title="Anode Free Lithium Metal Battery Design",
              journal="J2", year=2026, doi=None, source="openalex"),
    ]
    a = _done_analysis_with_papers(
        db, "종합 서술입니다 (Solid Electrolyte Interface Engineering Study).", papers,
    )
    a.sections_json = [
        {"name": "신소재", "body": "부분 서술입니다 (Anode Free Lithium Metal Battery Design)."}
    ]
    db.commit()

    body = client.get(f"/api/analyses/{a.id}").json()
    assert "[\\[1\\]](#ref-1)" in body["report_md"]
    assert body["sections"][0]["name"] == "신소재"
    assert "[\\[2\\]](#ref-2)" in body["sections"][0]["body_md"]
    assert len(body["references"]) == 2


def test_analysis_sections_empty_when_not_three_tier(client):
    db = app.dependency_overrides[get_db]()
    paper = Paper(paper_key="s3", title="Some Paper Title For This Test Case",
                  journal="J", year=2026, doi=None, source="openalex")
    a = _done_analysis_with_papers(db, "본문", [paper])

    assert client.get(f"/api/analyses/{a.id}").json()["sections"] == []


def test_subfield_analysis_lookup_defaults_to_kr(client):
    db = app.dependency_overrides[get_db]()
    a = _done_analysis_with_papers(db, "본문", [])
    r = client.get(f"/api/subfields/{a.subfield_id}/analyses/{a.year}")
    assert r.status_code == 200
    assert r.json()["country"] == "KR"
    assert r.json()["country_name"] == "한국"


def test_subfield_analysis_lookup_selects_by_country(client):
    """같은 세부기술·연도라도 국가가 다르면 다른 분석이다."""
    db = app.dependency_overrides[get_db]()
    kr = _done_analysis_with_papers(db, "한국 보고서", [])
    us = Analysis(subfield_id=kr.subfield_id, year=kr.year, status="done",
                  query_hash="h-us", report_md="미국 보고서", country="US")
    db.add(us)
    db.commit()

    got = client.get(
        f"/api/subfields/{kr.subfield_id}/analyses/{kr.year}?country=US"
    ).json()
    assert got["country"] == "US"
    assert got["country_name"] == "미국"
    assert "미국 보고서" in got["report_md"]


def test_year_list_is_scoped_to_the_same_country(client):
    """연도 목록에 다른 국가의 연도가 섞이면 이동 링크가 404로 간다."""
    db = app.dependency_overrides[get_db]()
    kr = _done_analysis_with_papers(db, "본문", [])
    db.add(Analysis(subfield_id=kr.subfield_id, year=kr.year + 1, status="done",
                    query_hash="h-us", report_md="미국", country="US"))
    db.commit()

    got = client.get(f"/api/subfields/{kr.subfield_id}/analyses/{kr.year}").json()
    assert got["years"] == [kr.year]


def test_admin_schedule_roundtrips_countries(client):
    """스케줄러가 돌 국가. 콤마 구분이고 기본은 KR이다."""
    h = {"X-Admin-Key": settings.admin_key}
    assert client.get("/api/admin/schedule", headers=h).json()["countries"] == "KR"

    client.put("/api/admin/schedule",
               json={"enabled": True, "day": 10, "hour": 3, "years_back": 1,
                     "countries": "KR,US"}, headers=h)
    assert client.get("/api/admin/schedule", headers=h).json()["countries"] == "KR,US"


def test_admin_schedule_rejects_malformed_country_list(client):
    """빈 값이나 형식이 어긋난 코드는 막는다 — 잘못 저장되면 스케줄러가 조용히
    존재하지 않는 국가로 검색을 돌려 0건을 받는다."""
    h = {"X-Admin-Key": settings.admin_key}
    r = client.put("/api/admin/schedule",
                   json={"enabled": True, "day": 10, "hour": 3, "years_back": 1,
                         "countries": "KR,USA"}, headers=h)
    assert r.status_code == 422


def _seed_countries(db, countries=("KR", "US"), *, year=2026, subfield_id=1):
    """비교 API 테스트용 — 지정 국가의 done 분석을 심는다.

    country를 반드시 명시한다: SQLAlchemy의 default=는 INSERT 시점에만 적용돼
    직접 만든 객체에는 안 들어간다.
    """
    for c in countries:
        db.add(Analysis(subfield_id=subfield_id, year=year, country=c, status="done",
                        query_hash="h", report_md=f"# {c} 보고서", stats_json={}))
    db.commit()


def test_get_comparison_404_before_generation(client):
    r = client.get("/api/subfields/1/comparison",
                   params={"year": 2026, "countries": "KR,US"})
    assert r.status_code == 404


def test_get_comparison_normalizes_country_order(client):
    """조회도 국가 순서를 정규화해야 큐잉한 행을 찾는다."""
    db = app.dependency_overrides[get_db]()
    _seed_countries(db)
    db.close()

    client.post(
        "/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
        json={"year": 2026, "comparisons": [{"subfield_id": 1, "countries": ["US", "KR"]}]},
    )

    r = client.get("/api/subfields/1/comparison",
                   params={"year": 2026, "countries": "KR,US"})
    assert r.status_code == 200
    body = r.json()
    assert body["countries"] == ["KR", "US"]
    assert body["country_names"] == ["한국", "미국"]
    # pending도 그대로 내려준다 — 화면이 status로 폴링을 판단한다
    assert body["status"] == "pending"
    assert body["subfield_name"] == "양자컴퓨팅"


def test_get_comparison_carries_pairwise_sections(client):
    db = app.dependency_overrides[get_db]()
    _seed_countries(db, ("KR", "CN"), year=2026)
    db.add(CountryComparison(subfield_id=1, year=2026, countries="CN,KR",
                             status="done", report_md="x",
                             sections_json=[{"name": "한국 vs 중국", "body": "본문"}],
                             generated_at=datetime(2026, 8, 4)))
    db.commit()
    db.close()

    got = client.get("/api/subfields/1/comparison",
                     params={"year": 2026, "countries": "KR,CN"}).json()
    assert got["sections"][0]["name"] == "한국 vs 중국"


def test_availability_lists_only_done_countries(client):
    """미보유 국가는 아예 내려주지 않는다 — 화면이 숨김으로 처리하기 위해서다."""
    db = app.dependency_overrides[get_db]()
    _seed_countries(db, ("KR", "CN"), year=2026)
    db.add(Analysis(subfield_id=1, year=2026, country="US", status="searching",
                    query_hash="h", stats_json={}))
    db.commit()
    db.close()

    r = client.get("/api/subfields/1/availability", params={"year": 2026})
    assert r.status_code == 200
    assert r.json()["countries"] == ["CN", "KR"]      # US는 done이 아니라 빠진다


def test_availability_lists_done_comparisons(client):
    db = app.dependency_overrides[get_db]()
    _seed_countries(db, ("KR", "CN"), year=2026)
    db.add(CountryComparison(subfield_id=1, year=2026, countries="CN,KR",
                             status="done", report_md="x",
                             generated_at=datetime(2026, 8, 4)))
    db.commit()
    db.close()

    got = client.get("/api/subfields/1/availability", params={"year": 2026}).json()
    # 이름표는 내려보내지 않는다 — CountryBar가 기준국을 빼고 자체 규칙으로 만든다.
    assert got["comparisons"] == [{"countries": ["CN", "KR"]}]


def _multi(db, *, sections=True):
    """3개국 비교 1건 — 쌍별 1:1이 sections_json 안에 들어 있는 실제 형태."""
    _seed_countries(db, ("KR", "CN", "JP"), year=2026)
    db.add(CountryComparison(
        subfield_id=1, year=2026, countries="CN,JP,KR", status="done",
        report_md="종합 본문", source_count=3, generated_at=datetime(2026, 8, 4),
        sections_json=[{"name": "한국 vs 중국", "body": "KR-CN 대조 본문"},
                       {"name": "한국 vs 일본", "body": "KR-JP 대조 본문"}] if sections else [],
    ))
    db.commit()


def test_pair_comparison_falls_back_to_the_section_inside_a_multi_report(client):
    """1:1을 따로 생성하지 않는 근거. 다국 비교가 쌍별을 이미 만들어 두므로
    KR,CN 요청은 그 섹션으로 넘긴다 — 없으면 이미 있는 내용을 다시 만들어야 한다."""
    db = app.dependency_overrides[get_db]()
    _multi(db)
    db.close()

    got = client.get("/api/subfields/1/comparison",
                     params={"year": 2026, "countries": "KR,CN"})
    assert got.status_code == 200
    assert got.json()["countries"] == ["KR", "CN"]        # 기준국이 앞
    assert got.json()["report_md"] == "KR-CN 대조 본문"
    assert got.json()["sections"] == []                   # 쌍은 더 펼칠 것이 없다


def test_pair_comparison_has_no_fallback_when_the_base_country_is_absent(client):
    """'중국 vs 일본'은 애초에 생성되지 않는다 — 쌍은 기준국을 한쪽에 고정한다."""
    db = app.dependency_overrides[get_db]()
    _multi(db)
    db.close()

    got = client.get("/api/subfields/1/comparison",
                     params={"year": 2026, "countries": "CN,JP"})
    assert got.status_code == 404


def test_exact_pair_row_wins_over_the_multi_fallback(client):
    """전용 1:1 행이 따로 있으면 그것을 그대로 준다(수동 큐잉 경로가 살아 있어야 한다)."""
    db = app.dependency_overrides[get_db]()
    _multi(db)
    db.add(CountryComparison(subfield_id=1, year=2026, countries="CN,KR", status="done",
                             report_md="전용 1:1 본문", generated_at=datetime(2026, 8, 4)))
    db.commit()
    db.close()

    got = client.get("/api/subfields/1/comparison",
                     params={"year": 2026, "countries": "KR,CN"}).json()
    assert got["report_md"] == "전용 1:1 본문"


def test_availability_surfaces_pairs_contained_in_a_multi_report(client):
    """폴백으로 갈 링크가 화면에 있어야 한다 — 없으면 조회 경로가 도달 불가능해진다."""
    db = app.dependency_overrides[get_db]()
    _multi(db)
    db.close()

    got = client.get("/api/subfields/1/availability", params={"year": 2026}).json()
    combos = [c["countries"] for c in got["comparisons"]]
    assert ["CN", "JP", "KR"] in combos      # 다국 자체
    assert ["CN", "KR"] in combos            # 안에 든 쌍
    assert ["JP", "KR"] in combos


def test_field_summary_rows_carry_countries(client):
    db = app.dependency_overrides[get_db]()
    _seed_countries(db, ("KR", "CN"), year=2026)
    db.close()

    rows = client.get("/api/fields/1/summary", params={"year": 2026}).json()["subfields"]
    assert rows[0]["countries"] == ["CN", "KR"]


def test_field_summary_reports_korea_numbers_not_other_country(client):
    """국가별로 분석이 여러 건이면 {subfield_id: analysis} 맵이 국가 무관 "아무 분석"을
    골라, 상태·건수가 다른 나라 것인데 행의 링크(analysis_id 무관, subfield_id 기준)는
    한국 보고서를 여는 어긋남이 생긴다(리뷰 지적). KR과 건수가 다른 US 분석을 함께
    심어 KR 쪽 숫자만 나오는지 고정한다."""
    db = app.dependency_overrides[get_db]()
    db.add(Analysis(subfield_id=1, year=2026, country="KR", status="done", query_hash="h",
                    report_md="# KR", stats_json={}, searched_count=10, analyzed_count=8))
    db.add(Analysis(subfield_id=1, year=2026, country="US", status="failed", query_hash="h",
                    report_md="", stats_json={}, searched_count=999, analyzed_count=777))
    db.commit()
    db.close()

    rows = client.get("/api/fields/1/summary", params={"year": 2026}).json()["subfields"]
    row = next(r for r in rows if r["subfield_id"] == 1)
    assert row["status"] == "done"
    assert row["searched_count"] == 10
    assert row["analyzed_count"] == 8


def test_field_years_counts_subfield_once_across_countries(client):
    """국가 필터가 없으면 세부기술 하나가 국가 수만큼 중복 집계된다(리뷰 지적:
    6개 세부기술 × 2개국이 "(12/24)"로 뜸). KR·US 두 국가를 심어도 세부기술 1개는
    1로만 세어져야 한다."""
    db = app.dependency_overrides[get_db]()
    _seed_countries(db, ("KR", "US"), year=2026)
    db.close()

    years = client.get("/api/fields/1/years").json()
    row = next(y for y in years if y["year"] == 2026)
    assert row["subfield_count"] == 1
    assert row["done_count"] == 1


def test_footnote_matches_title_truncated_with_ellipsis(client):
    """LLM이 긴 제목을 '...'로 잘라 인용하면 각주로 안 바뀌어 제목이 노출된다.

    실측(차세대 메모리반도체 KR 2025 재실행 후): 서술부 인용 10건 중 8건이 잘린
    형태라 각주가 36개에서 4개로 줄었다. 매처는 "DB 제목이 인용문에 포함되는가"를
    보는데 잘린 인용은 반대(인용문이 제목의 앞부분)라 걸리지 않았다.
    """
    db = app.dependency_overrides[get_db]()
    long_title = (
        "Enhanced Device Characteristics of Hybrid Channel Poly-Si IGO Structures "
        "with Interlayers by Suppressing Oxidation Induced Variability"
    )
    a = Analysis(subfield_id=1, year=2026, status="done", query_hash="h", stats_json={},
                 report_md=f"성과가 보고되었다 ({long_title[:60]}...).")
    db.add(a)
    db.flush()
    p = Paper(paper_key="pk-trunc", title=long_title, journal="Nature",
              year=2025, doi="10.1/trunc", source="openalex")
    db.add(p)
    db.flush()
    db.add(AnalysisPaper(analysis_id=a.id, paper_id=p.id))
    db.commit()
    aid = a.id
    db.close()

    r = client.get(f"/api/analyses/{aid}").json()
    assert "(#ref-1)" in r["report_md"]
    assert long_title[:60] not in r["report_md"]   # 제목이 노출되지 않는다
    assert len(r["references"]) == 1


def test_footnote_ignores_too_short_truncated_citation(client):
    """짧게 잘린 인용은 우연히 다른 논문의 앞부분과 겹칠 수 있어 치환하지 않는다."""
    db = app.dependency_overrides[get_db]()
    a = Analysis(subfield_id=1, year=2027, status="done", query_hash="h", stats_json={},
                 report_md="성과가 보고되었다 (Deep...).")
    db.add(a)
    db.flush()
    p = Paper(paper_key="pk-short", title="Deep Learning for Memory Devices",
              journal="Nature", year=2025, doi="10.1/short", source="openalex")
    db.add(p)
    db.flush()
    db.add(AnalysisPaper(analysis_id=a.id, paper_id=p.id))
    db.commit()
    aid = a.id
    db.close()

    r = client.get(f"/api/analyses/{aid}").json()
    assert "(Deep...)" in r["report_md"]   # 원문 그대로 둔다
    assert r["references"] == []


def test_footnote_splits_semicolon_separated_citations(client):
    """한 괄호에 ';'로 여러 논문을 나열한 인용을 각각의 각주로 바꾼다.

    실측(차세대 메모리반도체 KR 2025): 잘림 매칭을 고친 뒤에도 남은 미치환 8건 중
    5건이 이 형태였다. 괄호 안 전체를 하나의 제목으로 보면 어느 쪽과도 안 맞는다.
    """
    db = app.dependency_overrides[get_db]()
    t1 = "Emulating Nociceptor and Synaptic Functions in GaOx Resistive Memory"
    t2 = "Toward More Realistic Neuromorphic Devices with Oxide Semiconductors"
    a = Analysis(subfield_id=1, year=2028, status="done", query_hash="h", stats_json={},
                 report_md=f"연구가 보고되었다 ({t1}; {t2}).")
    db.add(a)
    db.flush()
    for i, t in enumerate((t1, t2), start=1):
        p = Paper(paper_key=f"pk-multi-{i}", title=t, journal="Nature",
                  year=2025, doi=f"10.1/m{i}", source="openalex")
        db.add(p)
        db.flush()
        db.add(AnalysisPaper(analysis_id=a.id, paper_id=p.id))
    db.commit()
    aid = a.id
    db.close()

    r = client.get(f"/api/analyses/{aid}").json()
    assert "(#ref-1)" in r["report_md"] and "(#ref-2)" in r["report_md"]
    assert len(r["references"]) == 2
    assert t1 not in r["report_md"] and t2 not in r["report_md"]


def test_footnote_keeps_partial_semicolon_match_intact(client):
    """일부만 매칭되면 통째로 원문을 둔다 — 매칭 안 된 쪽 텍스트가 사라지면 안 된다."""
    db = app.dependency_overrides[get_db]()
    t1 = "Emulating Nociceptor and Synaptic Functions in GaOx Resistive Memory"
    a = Analysis(subfield_id=1, year=2029, status="done", query_hash="h", stats_json={},
                 report_md=f"연구가 보고되었다 ({t1}; 알 수 없는 다른 논문 제목입니다).")
    db.add(a)
    db.flush()
    p = Paper(paper_key="pk-partial", title=t1, journal="Nature",
              year=2025, doi="10.1/p1", source="openalex")
    db.add(p)
    db.flush()
    db.add(AnalysisPaper(analysis_id=a.id, paper_id=p.id))
    db.commit()
    aid = a.id
    db.close()

    r = client.get(f"/api/analyses/{aid}").json()
    assert "알 수 없는 다른 논문 제목입니다" in r["report_md"]
    assert r["references"] == []


def _seed_metric_drilldown(db, *, year=2031):
    """지표 드릴다운용 — 논문 2건 + 같은 지표를 담은 추출 2건."""
    from app.models import PaperExtraction
    from app.services import mapper

    a = Analysis(subfield_id=1, year=year, status="done", query_hash="h", stats_json={})
    db.add(a)
    db.flush()
    rows = [
        ("pk-m1", "쿡 컨버터를 쓴 논문", "97.3", "Z-소스 쿡 컨버터"),
        ("pk-m2", "양자점 태양전지 논문", "1.67", "CdS 양자점 태양전지"),
    ]
    for key, title, value, target in rows:
        p = Paper(paper_key=key, title=title, journal="Nature", year=2025,
                  doi=f"10.1/{key}", source="openalex")
        db.add(p)
        db.flush()
        db.add(AnalysisPaper(analysis_id=a.id, paper_id=p.id))
        db.add(PaperExtraction(
            paper_key=key, subfield_id=1, tech_summary="요약",
            metrics_json=[{"name": "전력변환효율(PCE)", "value": value,
                           "unit": "%", "target": target}],
            model_ver=mapper.model_ver(),
        ))
    db.commit()
    return a.id


def test_metric_drilldown_lists_papers_behind_a_number(client):
    """표의 "PCE 447편"에서 어느 논문인지 볼 수 있어야 한다.

    이상값을 기계적으로 거를 수는 없으므로(같은 이름 아래 다른 물리량이 섞인다 —
    태양전지 PCE와 전력회로 변환효율) 지우는 대신 검증 가능하게 만든다.
    """
    db = app.dependency_overrides[get_db]()
    aid = _seed_metric_drilldown(db)
    db.close()

    r = client.get(f"/api/analyses/{aid}/metrics",
                   params={"name": "전력변환효율(PCE)", "unit": "%"})
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 2
    # 값 내림차순 — 이상값을 확인하려는 것이 이 화면의 목적이라 큰 값이 위로 온다.
    assert rows[0]["value"] == 97.3
    assert rows[0]["target"] == "Z-소스 쿡 컨버터"
    assert rows[0]["title"] == "쿡 컨버터를 쓴 논문"
    assert rows[0]["doi"] == "10.1/pk-m1"


def test_metric_drilldown_uses_the_same_normalization_as_the_table(client):
    """표와 같은 정규화(_metric_key)로 묶어야 숫자가 일치한다 —
    괄호·구분자 차이로 갈리면 "447편인데 목록은 12편"이 된다."""
    db = app.dependency_overrides[get_db]()
    aid = _seed_metric_drilldown(db, year=2032)
    db.close()

    # 괄호를 뺀 표기로 물어도 같은 그룹을 찾는다.
    r = client.get(f"/api/analyses/{aid}/metrics",
                   params={"name": "전력변환효율", "unit": "%"})
    assert len(r.json()["rows"]) == 2


def test_metric_drilldown_404_for_unknown_analysis(client):
    r = client.get("/api/analyses/99999/metrics", params={"name": "x", "unit": "%"})
    assert r.status_code == 404


# ── POST /admin/queue — 큐잉 통합 ──

def test_queue_requires_admin_key(client):
    assert client.post("/api/admin/queue", json={"year": 2026}).status_code == 401


def test_queue_accepts_an_empty_request(client):
    """화면이 아무것도 선택하지 않은 채 눌러도 200이어야 한다 — 빈 요청은 오류가 아니다."""
    r = client.post("/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
                    json={"year": 2026})
    assert r.status_code == 200
    assert r.json() == {
        "queued": {"analyses": 0, "comparisons": 0, "field_reports": 0, "roadmap_checks": 0},
        "skipped": [],
    }


def test_queue_enqueues_an_analysis_for_the_requested_country(client):
    r = client.post("/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
                    json={"year": 2026, "analyses": [{"subfield_id": 1, "country": "US"}]})
    assert r.status_code == 200
    assert r.json()["queued"]["analyses"] == 1
    assert r.json()["skipped"] == []

    db = app.dependency_overrides[get_db]()
    rows = db.query(Analysis).filter(Analysis.year == 2026).all()
    assert [(a.country, a.status) for a in rows] == [("US", "pending")]
    db.close()


def test_queue_rejects_a_malformed_country_without_stopping_the_request(client):
    """'us'는 정규화되지 않은 채 Analysis(country='us')로 저장되면 대시보드·비교
    어느 경로에서도 보이지 않는 고아 행이 된다(country == 'KR' 정확 비교, parse_countries
    대문자화) — 400이 아니라 skip이어야 한다(한 건이 나머지를 막지 않는 이 API의 규약)."""
    r = client.post("/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
                    json={"year": 2026, "analyses": [{"subfield_id": 1, "country": "대한민국"}]})
    body = r.json()
    assert body["queued"]["analyses"] == 0
    assert body["skipped"] == [
        {"kind": "analysis", "subfield_id": 1, "country": "대한민국",
         "reason": "국가 코드는 두 글자 알파벳이어야 합니다: 대한민국"}
    ]
    db = app.dependency_overrides[get_db]()
    assert db.query(Analysis).count() == 0
    db.close()


def test_queue_rejects_a_malformed_comparison_country_without_stopping_the_request(client):
    db = app.dependency_overrides[get_db]()
    _seed_countries(db, ("KR", "US"), year=2026)
    db.close()

    r = client.post(
        "/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
        json={"year": 2026, "comparisons": [{"subfield_id": 1, "countries": ["KR", "USA"]}]},
    )
    body = r.json()
    assert body["queued"]["comparisons"] == 0
    assert body["skipped"] == [
        {"kind": "comparison", "subfield_id": 1, "countries": ["KR", "USA"],
         "reason": "국가 코드는 두 글자 알파벳이어야 합니다: USA"}
    ]
    db = app.dependency_overrides[get_db]()
    assert db.query(CountryComparison).count() == 0
    db.close()


def test_queue_skips_a_done_analysis_without_force_but_requeues_with_it(client):
    """force는 프론트의 재생성 흐름이 기대는 유일한 스위치다(Finding 4). runner.enqueue는
    이미 done이고 query_hash가 그대로면 빈 리스트를 돌려준다 — force 없이는 그 사실이
    skip 사유로 남아야 하고(Finding 1), force를 주면 같은 행이 재큐잉돼야 한다."""
    db = app.dependency_overrides[get_db]()
    subfield = db.get(Subfield, 1)
    db.add(Analysis(subfield_id=1, year=2026, country="KR", status="done",
                    query_hash=search.query_hash(subfield, 2026, 2026, "KR"),
                    report_md="x", stats_json={}))
    db.commit()
    db.close()

    r = client.post("/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
                    json={"year": 2026, "analyses": [{"subfield_id": 1, "country": "KR"}]})
    body = r.json()
    assert body["queued"]["analyses"] == 0
    assert body["skipped"] == [
        {"kind": "analysis", "subfield_id": 1, "country": "KR",
         "reason": "이미 완료된 분석이고 검색식도 바뀌지 않았습니다 — "
                   "다시 실행하려면 강제 재실행을 선택하세요."}
    ]

    r2 = client.post("/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
                     json={"year": 2026,
                           "analyses": [{"subfield_id": 1, "country": "KR", "force": True}]})
    body2 = r2.json()
    assert body2["queued"]["analyses"] == 1
    assert body2["skipped"] == []

    db = app.dependency_overrides[get_db]()
    row = db.query(Analysis).filter(Analysis.subfield_id == 1, Analysis.year == 2026).one()
    assert row.status == "pending"
    db.close()


def test_queue_skips_a_missing_subfield_with_a_reason(client):
    """조용히 건너뛰지 않는다 — 왜 빠졌는지 화면이 말해야 한다."""
    r = client.post("/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
                    json={"year": 2026, "analyses": [{"subfield_id": 999, "country": "KR"}]})
    body = r.json()
    assert body["queued"]["analyses"] == 0
    assert body["skipped"] == [
        {"kind": "analysis", "subfield_id": 999, "country": "KR", "reason": "세부기술 없음"}
    ]


def _inactive_subfield(db) -> int:
    row = Subfield(field_id=1, name="비활성기술", query="q", active=False)
    db.add(row)
    db.commit()
    return row.id


def test_queue_refuses_an_inactive_subfield(client):
    """비활성은 "목록에서 감춘 것"이 아니라 "돌리지 않기로 한 것"이다.

    화면이 비활성 행을 선택 불가로 그리지만 그것만으로는 부족하다 — 다른 세션에서
    비활성화하면 이 화면의 오래된 선택이 그대로 넘어온다. 검색·추출은 돈이 나가므로
    서버에서 막는다.
    """
    db = app.dependency_overrides[get_db]()
    subfield_id = _inactive_subfield(db)
    db.close()

    r = client.post("/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
                    json={"year": 2026, "analyses": [{"subfield_id": subfield_id, "country": "KR"}]})
    body = r.json()
    assert body["queued"]["analyses"] == 0
    assert body["skipped"][0]["kind"] == "analysis"
    assert body["skipped"][0]["subfield_id"] == subfield_id
    assert "비활성" in body["skipped"][0]["reason"]

    db = app.dependency_overrides[get_db]()
    assert db.query(Analysis).count() == 0
    db.close()


def test_queue_keeps_going_when_one_subfield_is_inactive(client):
    """한 건이 막혀도 나머지는 큐잉한다 — 이 API의 규약."""
    db = app.dependency_overrides[get_db]()
    inactive_id = _inactive_subfield(db)
    db.close()

    r = client.post(
        "/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
        json={"year": 2026, "analyses": [
            {"subfield_id": inactive_id, "country": "KR"},
            {"subfield_id": 1, "country": "KR"},
        ]},
    )
    body = r.json()
    assert body["queued"]["analyses"] == 1
    assert len(body["skipped"]) == 1


def test_queue_enqueues_a_multi_country_comparison(client):
    db = app.dependency_overrides[get_db]()
    _seed_countries(db, ("KR", "US", "CN"), year=2026)
    db.close()

    r = client.post(
        "/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
        json={"year": 2026,
              "comparisons": [{"subfield_id": 1, "countries": ["KR", "US", "CN"]}]},
    )
    assert r.json()["queued"]["comparisons"] == 1

    db = app.dependency_overrides[get_db]()
    row = db.query(CountryComparison).one()
    assert row.countries == "CN,KR,US"      # 정렬 저장
    assert row.status == "pending"
    db.close()


def test_queue_reports_an_unknown_subfield_in_a_comparison(client):
    """없는 세부기술은 enqueue_comparison이 LookupError로 알린다 — 사유로 옮겨진다."""
    r = client.post(
        "/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
        json={"year": 2026, "comparisons": [{"subfield_id": 999, "countries": ["KR", "US"]}]},
    )
    body = r.json()
    assert body["queued"]["comparisons"] == 0
    assert body["skipped"][0]["kind"] == "comparison"
    assert body["skipped"][0]["subfield_id"] == 999


def test_queue_reports_why_a_comparison_was_skipped(client):
    """상대국 분석이 없으면 만들 수 없다. 그 사실이 화면에 문장으로 나와야 한다."""
    db = app.dependency_overrides[get_db]()
    _seed_countries(db, ("KR",), year=2026)     # US 없음
    db.close()

    r = client.post(
        "/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
        json={"year": 2026, "comparisons": [{"subfield_id": 1, "countries": ["KR", "US"]}]},
    )
    body = r.json()
    assert body["queued"]["comparisons"] == 0
    assert body["skipped"][0]["kind"] == "comparison"
    assert body["skipped"][0]["subfield_id"] == 1
    assert "US" in body["skipped"][0]["reason"]


def test_queue_keeps_going_after_one_item_fails(client):
    """한 건이 막혀도 나머지는 큐잉한다."""
    db = app.dependency_overrides[get_db]()
    _seed_countries(db, ("KR", "US"), year=2026)
    sf2 = Subfield(field_id=1, name="두 번째", query="q")
    db.add(sf2)
    db.commit()
    sf2_id = sf2.id
    db.close()

    r = client.post(
        "/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
        json={"year": 2026, "comparisons": [
            {"subfield_id": sf2_id, "countries": ["KR", "US"]},   # 분석 없음 → skip
            {"subfield_id": 1, "countries": ["KR", "US"]},        # 정상
        ]},
    )
    body = r.json()
    assert body["queued"]["comparisons"] == 1
    assert len(body["skipped"]) == 1


def test_queue_skips_analyses_when_the_openalex_budget_is_exhausted(client):
    """예산이 소진된 채 분석을 큐잉하면 잡 루프가 건마다 count_only(건당 $0.001)를
    한 번 쓰고 paused로 내려간다 — search.collect가 예산 게이트보다 먼저 부르기
    때문이다. 큐잉 시점에 막는다."""
    db = app.dependency_overrides[get_db]()
    _exhaust_budget(db)
    db.close()

    r = client.post("/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
                    json={"year": 2026, "analyses": [{"subfield_id": 1, "country": "KR"}]})
    body = r.json()
    assert body["queued"]["analyses"] == 0
    assert body["skipped"][0]["kind"] == "analysis"
    assert "예산" in body["skipped"][0]["reason"]

    db = app.dependency_overrides[get_db]()
    assert db.query(Analysis).count() == 0
    db.close()


def test_queue_enqueues_a_field_report(client):
    _seed_done_analysis(client, "세부기술 A", "## 성과\nA 본문")

    r = client.post("/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
                    json={"year": 2026, "field_reports": [1]})
    assert r.json()["queued"]["field_reports"] == 1

    db = app.dependency_overrides[get_db]()
    assert db.query(FieldReport).one().status == "pending"
    db.close()


def test_queue_reports_why_a_roadmap_check_was_skipped(client):
    """로드맵이 미등록이면 점검을 만들 수 없다 — 분야 탭이 바로 옆 칸에서 [등록]을
    안내할 수 있도록 사유가 내려와야 한다."""
    _seed_done_analysis(client, "세부기술 A", "## 성과\nA 본문")

    r = client.post("/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
                    json={"year": 2026, "roadmap_checks": [1]})
    body = r.json()
    assert body["queued"]["roadmap_checks"] == 0
    assert body["skipped"][0]["kind"] == "roadmap_check"
    assert body["skipped"][0]["field_id"] == 1
    assert "로드맵" in body["skipped"][0]["reason"]


def test_queue_handles_all_four_kinds_in_one_request(client):
    """이 API의 존재 이유 — 화면의 '선택한 N건 생성'이 호출 한 번이어야 한다."""
    db = app.dependency_overrides[get_db]()
    _seed_countries(db, ("KR", "US"), year=2026)
    db.close()
    _seed_done_analysis(client, "세부기술 A", "## 성과\nA 본문")

    r = client.post(
        "/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
        json={
            "year": 2026,
            "analyses": [{"subfield_id": 1, "country": "JP"}],
            "comparisons": [{"subfield_id": 1, "countries": ["KR", "US"]}],
            "field_reports": [1],
            "roadmap_checks": [1],          # 로드맵 미등록 → skip
        },
    )
    body = r.json()
    assert body["queued"] == {"analyses": 1, "comparisons": 1,
                              "field_reports": 1, "roadmap_checks": 0}
    assert [s["kind"] for s in body["skipped"]] == ["roadmap_check"]


def test_queue_isolates_a_one_country_comparison_instead_of_rejecting_everything(client):
    """항목 하나가 잘못돼도 나머지는 큐잉해야 한다 — 이 API의 존재 이유다.

    스키마에 min_length=2가 걸려 있으면 Pydantic이 요청 본문 전체를 422로 막아,
    같이 보낸 분야 보고서까지 통째로 사라진다.
    """
    _seed_done_analysis(client, "세부기술 A", "## 성과\nA 본문")

    r = client.post(
        "/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
        json={
            "year": 2026,
            "comparisons": [{"subfield_id": 1, "countries": ["KR"]}],   # 1개국 — 만들 수 없다
            "field_reports": [1],                                       # 이건 살아야 한다
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["queued"]["field_reports"] == 1
    assert body["queued"]["comparisons"] == 0
    assert body["skipped"][0]["kind"] == "comparison"
    assert "2개" in body["skipped"][0]["reason"]


def test_queue_isolates_an_empty_comparison_country_list(client):
    """빈 목록도 같은 취급 — 요청 전체를 죽이지 않는다."""
    r = client.post(
        "/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
        json={"year": 2026, "comparisons": [{"subfield_id": 1, "countries": []}]},
    )
    assert r.status_code == 200
    assert r.json()["queued"]["comparisons"] == 0
    assert r.json()["skipped"][0]["kind"] == "comparison"


def test_dashboard_carries_comparison_status_keyed_by_year(client):
    """세부기술 탭이 응답 하나로 그려지려면 분석과 비교가 같은 응답에 있어야 한다.
    둘을 따로 부르면 두 응답의 연도·국가가 어긋날 여지가 생긴다."""
    db = app.dependency_overrides[get_db]()
    _seed_countries(db, ("KR", "CN", "JP"), year=2026)
    db.add(CountryComparison(
        subfield_id=1, year=2026, countries="CN,JP,KR", status="done",
        report_md="종합", generated_at=datetime(2026, 8, 4),
        sections_json=[{"name": "한국 vs 중국", "body": "b"}],
    ))
    db.commit()
    db.close()

    rows = client.get("/api/admin/dashboard",
                      headers={"X-Admin-Key": settings.admin_key}).json()["rows"]
    row = next(r for r in rows if r["subfield_id"] == 1)
    assert row["comparisons"]["2026"]["CN,JP,KR"] == "done"
    # 다국 안에 든 1:1은 별도 행이 없다 — 미생성으로 두면 이미 있는 것을 다시 만든다.
    assert row["comparisons"]["2026"]["CN,KR"] == "in_multi"
    assert row["comparisons"]["2026"]["JP,KR"] == "in_multi"


def test_dashboard_gives_an_empty_comparison_map_when_there_are_none(client):
    rows = client.get("/api/admin/dashboard",
                      headers={"X-Admin-Key": settings.admin_key}).json()["rows"]
    assert rows[0]["comparisons"] == {}
