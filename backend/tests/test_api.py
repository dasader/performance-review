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
from app.models.field import Field, Subfield
from app.models.paper import Paper
from app.routers import admin as admin_module


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


def test_admin_accepts_correct_key(client):
    r = client.get("/api/admin/dashboard", headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 200


def test_dashboard_includes_schedule_info(client):
    r = client.get("/api/admin/dashboard", headers={"X-Admin-Key": settings.admin_key})
    schedule = r.json()["schedule"]
    assert schedule["enabled"] is True
    assert schedule["next_run_at"]  # ISO 문자열
    assert schedule["last_run_at"] is None  # 아직 자동 실행된 적 없음
    assert schedule["last_run_queued_count"] is None


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
