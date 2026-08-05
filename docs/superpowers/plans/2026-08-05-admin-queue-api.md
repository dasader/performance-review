# 관리자 큐잉 API 통합 (1단계) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 네 종류 산출물(세부기술 분석·국가 비교·분야 종합·로드맵 점검)의 큐잉을 `POST /api/admin/queue` 하나로 모으고, `GET /api/admin/dashboard`에 비교 상태를 실어 세부기술 탭이 응답 하나로 그려지게 한다.

**Architecture:** 새 엔드포인트는 기존 `runner.enqueue` / `comparison.enqueue_comparison` / `reducer.enqueue_field_report` / `reducer.enqueue_roadmap_check`를 그대로 부르는 얇은 조립층이다. 새 도메인 로직도 DB 스키마 변경도 없다. 이 단계에서는 **화면을 건드리지 않는다** — 기존 탭이 그대로 동작하고, 2단계 프론트 작업이 이 API 위에 올라간다.

**Tech Stack:** FastAPI · SQLAlchemy · Pydantic v2 · pytest (인메모리 sqlite)

## Global Constraints

- 백엔드 테스트는 반드시 `backend/.venv`를 쓴다 — 시스템 파이썬에는 의존성이 없다.
  **워크트리에는 `.venv`가 없다**(메인 체크아웃에만 있다). 워크트리에서 실행할 때는
  `backend/`에서 절대 경로로 부른다:
  `/home/dev/code/performance-review/backend/.venv/bin/python -m pytest -q`
  아래 모든 Run 단계의 `./.venv/bin/python`을 이 경로로 바꿔 읽는다.
- 백엔드 린터는 없다. 프론트만 oxlint를 쓰며 이 단계는 프론트를 건드리지 않는다.
- 커밋 메시지는 한국어로 쓰고 **왜**를 남긴다(이 저장소의 기존 커밋 관례).
- 하나가 막혀도 나머지는 큐잉한다 — `field-reports/run-all`이 세운 규약. 다만 조용히 건너뛰지 않고 `skipped`에 사유를 담는다.
- `runner.enqueue` · `comparison.enqueue_comparison` · `reducer.enqueue_field_report` · `reducer.enqueue_roadmap_check`는 **전부 내부에서 `db.commit()`을 한다.** 핸들러에서 다시 커밋하지 않는다.
- 이 단계에서 기존 엔드포인트를 **지우지 않는다**(4단계에서 지운다). 화면이 아직 옛 API를 쓰고 있다.

---

### Task 1: `POST /api/admin/queue` — 골격과 분석 큐잉

**Files:**
- Modify: `backend/app/routers/admin.py` (요청 모델은 `RunIn` 아래, 핸들러는 `/dashboard` 앞)
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `runner.enqueue(db, subfield, year_from, year_to, *, force, trigger="manual", country="KR") -> list[Analysis]`, `budget.spent_today(db) -> float`, `budget.reset_time_utc() -> datetime`
- Produces: `POST /api/admin/queue` — 본문 `{year, analyses[], comparisons[], field_reports[], roadmap_checks[]}`, 응답 `{"queued": {"analyses": int, "comparisons": int, "field_reports": int, "roadmap_checks": int}, "skipped": [{"kind": str, "subfield_id"|"field_id": int, "reason": str}]}`. Task 2·3이 같은 핸들러에 블록을 덧붙이고, Task 4와 2단계 프론트가 이 응답 모양에 의존한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_api.py` 맨 끝에 추가한다.

```python
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


def test_queue_skips_a_missing_subfield_with_a_reason(client):
    """조용히 건너뛰지 않는 것이 run-all과 다른 점이다 — 왜 빠졌는지 화면이 말해야 한다."""
    r = client.post("/api/admin/queue", headers={"X-Admin-Key": settings.admin_key},
                    json={"year": 2026, "analyses": [{"subfield_id": 999, "country": "KR"}]})
    body = r.json()
    assert body["queued"]["analyses"] == 0
    assert body["skipped"] == [
        {"kind": "analysis", "subfield_id": 999, "reason": "세부기술 없음"}
    ]


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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_api.py -k queue -v`
Expected: FAIL — 전부 404(`/api/admin/queue` 없음). `test_queue_requires_admin_key`도 404라 실패한다.

- [ ] **Step 3: 요청 모델을 추가한다**

`backend/app/routers/admin.py`의 `class RunIn(...)` 블록 **바로 아래**에 넣는다.

```python
class QueueAnalysisIn(BaseModel):
    subfield_id: int
    # ISO 3166-1 alpha-2. 화면의 국가 열 한 칸이 이 항목 하나에 대응한다.
    country: str = "KR"
    force: bool = False


class QueueComparisonIn(BaseModel):
    subfield_id: int
    # 다국 비교 하나만 만든다 — 1:1은 그 안의 섹션으로 조회된다(2026-08-04 설계).
    countries: list[str] = PydanticField(min_length=2)


class QueueIn(BaseModel):
    """관리자 화면에서 체크한 셀들을 한 요청으로 큐잉한다.

    네 종류를 한 번에 받는 이유: 화면의 "선택한 N건 생성"이 호출 한 번이어야 하고,
    부분 실패 집계를 화면마다 따로 하지 않기 위해서다. 종류별로 나누면 15건 선택에
    왕복이 15번 나가고 어디까지 성공했는지를 프론트가 스스로 조립해야 한다.
    """

    year: int = PydanticField(ge=1900, le=2100)
    analyses: list[QueueAnalysisIn] = []
    comparisons: list[QueueComparisonIn] = []
    field_reports: list[int] = []      # field_id
    roadmap_checks: list[int] = []     # field_id
```

- [ ] **Step 4: 핸들러를 추가한다**

같은 파일에서 `@router.get("/dashboard")` **바로 위**에 넣는다.

```python
@router.post("/queue")
def queue(payload: QueueIn, db: Session = Depends(get_db)):
    """체크한 대상들을 한 번에 큐잉하고, 건너뛴 것은 사유와 함께 돌려준다.

    하나가 막혀도 나머지는 큐잉한다(field-reports/run-all의 규약). **조용히 건너뛰지
    않는 것**이 run-all과 다른 점이다 — "10건 큐잉, 3건 건너뜀"만으로는 상대국 분석이
    없어서인지 로드맵이 미등록이어서인지 알 수 없었다.

    enqueue 계열 함수들이 각자 db.commit()을 하므로 여기서 다시 커밋하지 않는다.
    """
    queued = {"analyses": 0, "comparisons": 0, "field_reports": 0, "roadmap_checks": 0}
    skipped: list[dict] = []

    # 예산이 이미 소진됐으면 분석은 큐잉하지 않는다. 큐잉해 두면 잡 루프가 건마다
    # count_only(건당 $0.001)를 한 번 쓰고 paused로 내려간다 — search.collect가
    # 예산 게이트보다 먼저 부르기 때문이다(페이징 전에 게이트를 통과시키려는 설계).
    # 보고서류는 OpenAlex를 쓰지 않으므로 같은 요청 안에서도 그대로 처리한다.
    over_budget = bool(payload.analyses) and (
        budget.spent_today(db) >= settings.openalex_daily_budget_usd
    )
    for item in payload.analyses:
        if over_budget:
            skipped.append({
                "kind": "analysis",
                "subfield_id": item.subfield_id,
                "reason": (
                    f"OpenAlex 일일 예산 소진 — UTC "
                    f"{budget.reset_time_utc():%Y-%m-%d %H:%M} 이후 재시도하세요."
                ),
            })
            continue
        subfield = db.get(Subfield, item.subfield_id)
        if subfield is None:
            skipped.append({"kind": "analysis", "subfield_id": item.subfield_id,
                            "reason": "세부기술 없음"})
            continue
        rows = runner.enqueue(
            db, subfield, payload.year, payload.year,
            force=item.force, country=item.country,
        )
        queued["analyses"] += len(rows)

    return {"queued": queued, "skipped": skipped}
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_api.py -k queue -v`
Expected: PASS — 5건 전부.

- [ ] **Step 6: 전체 스위트를 돌린다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: 기존 379건 + 신규 5건 = 384 passed.

- [ ] **Step 7: 커밋**

```bash
git add backend/app/routers/admin.py backend/tests/test_api.py
git commit -m "feat: POST /admin/queue — 분석 큐잉과 사유 있는 skipped

화면의 '선택한 N건 생성'을 호출 한 번으로 만들기 위한 통합 큐잉 API의 첫 조각.
run-all이 조용히 건너뛰던 것을 skipped에 사유와 함께 담는다.

예산 소진 시 분석을 큐잉하지 않는 이유: search.collect가 예산 게이트보다 먼저
count_only(건당 \$0.001)를 부르므로, 큐잉해 두면 건마다 한 번씩 돈을 쓰고
paused로 내려간다. 보고서류는 OpenAlex를 쓰지 않으므로 같은 요청에서 계속 처리한다."
```

---

### Task 2: 국가 비교 큐잉

**Files:**
- Modify: `backend/app/routers/admin.py` (Task 1의 `queue` 핸들러)
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: Task 1의 `queue` 핸들러와 `QueueComparisonIn`, 그리고 `comparison.enqueue_comparison(db, subfield_id, year, countries: list[str]) -> CountryComparison` (없는 세부기술이면 `LookupError`, 국가 2개 미만·상대국 분석 없음이면 `ValueError`)
- Produces: `queued["comparisons"]` 집계와 `{"kind": "comparison", "subfield_id": int, "reason": str}` 형태의 skipped 항목

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_api.py`의 Task 1 테스트 아래에 추가한다.

```python
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
    """한 건이 막혀도 나머지는 큐잉한다 — run-all이 세운 규약."""
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_api.py -k queue -v`
Expected: 새 3건 FAIL — `queued["comparisons"]`가 항상 0이다(핸들러가 아직 comparisons를 읽지 않는다).

- [ ] **Step 3: 핸들러에 비교 블록을 추가한다**

`queue` 핸들러의 분석 `for` 루프 **아래**, `return` **위**에 넣는다.

```python
    for item in payload.comparisons:
        try:
            comparison.enqueue_comparison(db, item.subfield_id, payload.year, item.countries)
            queued["comparisons"] += 1
        except (LookupError, ValueError) as e:
            skipped.append({"kind": "comparison", "subfield_id": item.subfield_id,
                            "reason": str(e)})
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_api.py -k queue -v`
Expected: PASS — 8건 전부.

- [ ] **Step 5: 커밋**

```bash
git add backend/app/routers/admin.py backend/tests/test_api.py
git commit -m "feat: /admin/queue에 국가 비교 큐잉

enqueue_comparison의 LookupError/ValueError를 skipped의 reason으로 그대로 옮긴다 —
'상대국 분석 없음'을 화면이 문장으로 보여줄 수 있어야 한다."
```

---

### Task 3: 분야 종합·로드맵 점검 큐잉

**Files:**
- Modify: `backend/app/routers/admin.py` (Task 2까지의 `queue` 핸들러)
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: Task 2까지의 `queue` 핸들러, `reducer.enqueue_field_report(db, field_id, year) -> FieldReport`, `reducer.enqueue_roadmap_check(db, field_id, year) -> RoadmapCheck` (둘 다 없는 분야면 `LookupError`, 입력 부족이면 `ValueError`)
- Produces: `queued["field_reports"]` · `queued["roadmap_checks"]` 집계와 `{"kind": "field_report"|"roadmap_check", "field_id": int, "reason": str}` 형태의 skipped 항목. 2단계 분야 탭이 이 두 키를 쓴다.

- [ ] **Step 1: `FieldReport` import를 추가한다**

`backend/tests/test_api.py:15`의 import에 `FieldReport`를 넣는다. 이 파일은 아직
`CountryComparison, Field, Subfield`만 가져오고 있어 아래 테스트가 `NameError`로 죽는다.

```python
from app.models.field import CountryComparison, Field, FieldReport, Subfield
```

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`backend/tests/test_api.py`의 Task 2 테스트 아래에 추가한다. `_seed_done_analysis`는 이 파일에 이미 있는 헬퍼다(분야 1에 세부기술 + done 분석을 심는다).

```python
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
```

- [ ] **Step 3: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_api.py -k queue -v`
Expected: 새 3건 FAIL — `field_reports`·`roadmap_checks` 집계가 항상 0이다.

- [ ] **Step 4: 핸들러에 분야 블록을 추가한다**

비교 `for` 루프 **아래**, `return` **위**에 넣는다.

```python
    # 분야 산출물 두 종류는 큐잉 함수 이름과 집계 키만 다르고 실패 처리가 같다.
    for kind, field_ids, enqueue_one in (
        ("field_report", payload.field_reports, reducer.enqueue_field_report),
        ("roadmap_check", payload.roadmap_checks, reducer.enqueue_roadmap_check),
    ):
        for field_id in field_ids:
            try:
                enqueue_one(db, field_id, payload.year)
                queued[f"{kind}s"] += 1
            except (LookupError, ValueError) as e:
                skipped.append({"kind": kind, "field_id": field_id, "reason": str(e)})
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_api.py -k queue -v`
Expected: PASS — 11건 전부.

- [ ] **Step 6: 전체 스위트를 돌린다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: 390 passed (379 + 11).

- [ ] **Step 7: 커밋**

```bash
git add backend/app/routers/admin.py backend/tests/test_api.py
git commit -m "feat: /admin/queue에 분야 종합·로드맵 점검 큐잉

네 종류가 한 요청으로 모였다. 두 분야 산출물은 큐잉 함수와 집계 키만 다르고
실패 처리가 같아 튜플 순회로 묶었다."
```

---

### Task 4: `dashboard`에 비교 상태 싣기

**Files:**
- Modify: `backend/app/routers/admin.py:265-310` (`dashboard` 핸들러)
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `comparison.base_country(codes: list[str]) -> str`
- Produces: `dashboard` 응답의 각 row에 `"comparisons": {"<year>": {"<정렬된 국가키>": "<status|in_multi>"}}`. 2단계 세부기술 탭이 연도 필터로 이 맵을 골라 비교 열을 그린다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_api.py`의 Task 3 테스트 아래에 추가한다.

```python
def test_dashboard_carries_comparison_status_keyed_by_year(client):
    """세부기술 탭이 응답 하나로 그려지려면 분석과 비교가 같은 응답에 있어야 한다.
    지금은 comparison-grid를 따로 불러야 하고 그쪽은 한 연도만 준다."""
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_api.py -k dashboard -v`
Expected: FAIL — `KeyError: 'comparisons'`.

- [ ] **Step 3: dashboard에 비교 조회를 추가한다**

`dashboard` 핸들러에서 `by_subfield`를 채우는 `for` 루프 **아래**, `rows = []` **위**에 넣는다.

```python
    # 비교 상태도 같은 응답에 싣는다 — 세부기술 탭이 분석과 비교를 한 표에 그리므로
    # 두 번 부르면 두 응답의 연도·국가가 어긋날 여지가 생긴다. 연도별로 나눠 담아
    # 화면의 연도 필터와 행 펼침이 추가 요청 없이 동작하게 한다.
    comparisons: dict[int, dict[int, dict[str, str]]] = {}
    for c in db.query(
        CountryComparison.subfield_id, CountryComparison.year,
        CountryComparison.countries, CountryComparison.status,
    ).all():
        cells = comparisons.setdefault(c.subfield_id, {}).setdefault(c.year, {})
        cells[c.countries] = c.status
        # 3개국 이상 비교는 쌍별 1:1을 먼저 만들어 sections_json에 담고 종합한다
        # (process_comparison). 그 쌍을 미생성으로 두면 이미 있는 것을 다시 만든다.
        codes = c.countries.split(",")
        if c.status == "done" and len(codes) > 2:
            base = comparison.base_country(codes)
            for other in codes:
                if other != base:
                    cells.setdefault(",".join(sorted((base, other))), "in_multi")
```

그리고 `rows.append({...})`의 `"years": [...]` **다음 줄**에 추가한다.

```python
            # JSON 객체 키는 문자열이어야 한다.
            "comparisons": {
                str(year): cells
                for year, cells in comparisons.get(subfield.id, {}).items()
            },
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_api.py -k dashboard -v`
Expected: PASS.

- [ ] **Step 5: 전체 스위트를 돌린다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: 392 passed (390 + 2).

- [ ] **Step 6: 커밋**

```bash
git add backend/app/routers/admin.py backend/tests/test_api.py
git commit -m "feat: dashboard에 비교 상태를 연도별로 싣는다

세부기술 탭이 분석과 비교를 한 표에 그리므로 응답도 하나여야 한다 — 두 번 부르면
두 응답의 연도·국가가 어긋날 여지가 생긴다. comparison-grid는 4단계에서 지운다."
```

---

## 1단계 완료 조건

- `cd backend && ./.venv/bin/python -m pytest -q` → **392 passed**
- 화면 동작 변화 **없음**(기존 탭이 옛 API를 그대로 쓴다)
- `POST /api/admin/queue`와 확장된 `GET /api/admin/dashboard`가 2단계 프론트 작업의 입력으로 준비됨

## 이 계획에 없는 것

2~4단계(세부기술 탭 · 분야 탭 · 구 엔드포인트 제거)는 **각자 별도 계획으로 쓴다.** 프론트 작업은 이 단계가 확정한 응답 모양 위에 올라가므로, 지금 미리 쓰면 추측이 된다. 1단계가 머지되면 실제 응답을 보고 2단계 계획을 쓴다.
