# 국가 파라미터화 Implementation Plan (4단계)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax로 tracking.

**Goal:** 지금 KR로 하드코딩된 수집·집계·보고서 경로에 국가를 파라미터로 넣어, 같은 세부기술을
US·CN 등 다른 국가로도 분석할 수 있게 한다.

**Architecture:** `papers`와 `paper_extractions`는 **이미 국가 중립**이다(추출 캐시 키가
`paper_key + subfield_id + model_ver`). 그래서 국가는 `Analysis`에만 붙이면 되고,
공동연구 논문이 여러 국가 분석에 걸려도 **추출 LLM 비용이 한 번만** 든다. 기존 KR 분석은
`country DEFAULT 'KR'`로 자동 귀속되어 재실행이 필요 없다.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Alembic / pytest · React 19 + Vite + Tailwind

## Global Constraints

- 테스트는 반드시 `backend/.venv`를 쓴다: `cd backend && ./.venv/bin/python -m pytest`
- **`EXTRACTION_SCHEMA_VERSION`을 올리지 않는다.** `MAP_INSTRUCTION`에서 "한국"을 빼도
  `model_ver`는 프롬프트 텍스트를 보지 않아 캐시가 유지된다. 올리면 22,603건이 근거 없이
  전량 재추출된다.
- 마이그레이션 head는 현재 **0016**이다. 새 리비전은 **0017**이며 `down_revision="0016"`.
- **기본 국가는 항상 `"KR"`이다.** 모든 새 파라미터에 기본값을 두어, 화면·API를 고치기 전에도
  기존 동작이 그대로여야 한다.
- 55개 검색식은 **바뀌지 않는다.** 국가는 검색식이 아니라 OpenAlex 서버측 필터다.
- 프론트 레이아웃 간격은 4/8/12/16/24/40(Tailwind `1·2·3·4·6·10`)만 쓴다.

## PR 분할

- **PR A = Task 1~6** (백엔드 파라미터화). 기본값 `KR` 덕분에 이것만 머지해도 동작이 그대로다.
- **PR B = Task 7~8** (API·화면). 국가를 실제로 고를 수 있게 한다.

---

### Task 1: 마이그레이션 0017 + 모델

**Files:**
- Create: `backend/alembic/versions/0017_country.py`
- Modify: `backend/app/models/analysis.py`, `backend/app/models/paper.py`, `backend/app/models/schedule.py`
- Test: `backend/tests/test_models.py`

**Interfaces:**
- Produces: `Analysis.country: str`(기본 `"KR"`), `Paper.lead_countries_json: list`,
  `ScheduleSetting.countries: str`(콤마 구분, 기본 `"KR"`). `Paper.korea_flag` **삭제**.

`korea_flag`를 지우는 근거: 전수 grep 결과 `search.py`·`openalex.py`·`kci.py`에서
**쓰기만 하고 읽는 곳이 한 군데도 없다.** 국가가 파라미터가 되면 "한국인가"라는 단일
불리언은 의미가 사라지고, 같은 정보는 `countries_json`에 이미 있다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_models.py` 끝에 추가:

```python
def test_analysis_country_defaults_to_kr_and_is_unique_per_year():
    """기존 KR 분석이 자동 귀속되도록 기본값을 KR로 둔다. 같은 세부기술·연도라도
    국가가 다르면 별도 행이어야 한다."""
    from sqlalchemy.exc import IntegrityError as IE
    db = _session()
    a = Analysis(subfield_id=1, year=2025, status="pending", query_hash="h")
    db.add(a)
    db.commit()
    db.refresh(a)
    assert a.country == "KR"

    db.add(Analysis(subfield_id=1, year=2025, status="pending", query_hash="h",
                    country="US"))
    db.commit()          # 국가가 다르면 통과해야 한다

    db.add(Analysis(subfield_id=1, year=2025, status="pending", query_hash="h",
                    country="US"))
    with pytest.raises(IE):
        db.commit()      # 같은 (세부기술, 연도, 국가)는 막혀야 한다


def test_paper_has_lead_countries_and_no_korea_flag():
    """lead_countries_json은 교신저자 소속국이다. korea_flag는 읽는 곳이 없어 지웠다."""
    db = _session()
    p = Paper(paper_key="k", title="T", source="openalex",
              countries_json=["KR", "US"], lead_countries_json=["KR"])
    db.add(p)
    db.commit()
    db.refresh(p)
    assert p.lead_countries_json == ["KR"]
    assert not hasattr(p, "korea_flag")


def test_schedule_setting_has_countries():
    from app.models.schedule import ScheduleSetting
    db = _session()
    row = ScheduleSetting(id=1, enabled=True, day=10, hour=3, years_back=1)
    db.add(row)
    db.commit()
    db.refresh(row)
    assert row.countries == "KR"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_models.py -k "country or lead_countries" -v`
Expected: FAIL — `TypeError: 'country' is an invalid keyword argument`

- [ ] **Step 3: 모델을 고친다**

`backend/app/models/analysis.py` — `__table_args__`와 컬럼:

```python
    __table_args__ = (
        UniqueConstraint("subfield_id", "year", "country", name="uq_analysis_year"),
    )
```

`year` 컬럼 아래에 추가:

```python
    # ISO 3166-1 alpha-2. 기존 행은 마이그레이션이 'KR'로 채운다 — 재실행이 필요 없다.
    # papers/paper_extractions는 국가 중립이라(추출 캐시 키가 paper_key+subfield_id+
    # model_ver) 공동연구 논문이 여러 국가 분석에 걸려도 추출 비용은 한 번만 든다.
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="KR")
```

`backend/app/models/paper.py` — `korea_flag` 줄을 **삭제**하고 그 자리에:

```python
    # 교신저자(authorships[].is_corresponding)의 소속국 코드. countries_json이
    # "참여"라면 이쪽은 "주도"다 — 실측으로 일본 논문의 47%가 자국이 주도하지 않은
    # 국제공동연구라, 둘을 구분하지 않으면 국가별 숫자를 같은 의미로 오독한다.
    # OpenAlex authorships에 이미 들어 있어 추가 API 호출이 없다(보유율 91~94%).
    lead_countries_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
```

`Boolean` import가 더 이상 필요 없으면 함께 정리한다.

`backend/app/models/schedule.py` — `ScheduleSetting`의 `years_back` 아래:

```python
    # 스케줄러가 돌 국가. 콤마 구분("KR,US,CN"). 기본 KR이라 켜기 전에는 현행과 같다.
    countries: Mapped[str] = mapped_column(String(100), nullable=False, default="KR")
```

- [ ] **Step 4: 마이그레이션을 만든다**

`backend/alembic/versions/0017_country.py`:

```python
"""국가 파라미터화 — analyses.country, papers.lead_countries_json, schedule_settings.countries

기존 분석은 전부 한국 대상이므로 country='KR'로 채운다 — 재실행이 필요 없다.
분석 유일키에 country를 더해 같은 세부기술·연도를 국가별로 따로 둘 수 있게 한다.

papers.korea_flag는 삭제한다. 전수 grep 결과 쓰기만 하고 읽는 곳이 한 군데도 없었고,
국가가 파라미터가 되면 "한국인가"라는 단일 불리언은 의미가 사라진다(같은 정보는
countries_json에 있다).

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column("country", sa.String(length=2), nullable=False, server_default="KR"),
    )
    op.drop_constraint("uq_analysis_year", "analyses", type_="unique")
    op.create_unique_constraint(
        "uq_analysis_year", "analyses", ["subfield_id", "year", "country"]
    )
    op.add_column(
        "papers",
        sa.Column("lead_countries_json", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.drop_column("papers", "korea_flag")
    op.add_column(
        "schedule_settings",
        sa.Column("countries", sa.String(length=100), nullable=False, server_default="KR"),
    )


def downgrade() -> None:
    op.drop_column("schedule_settings", "countries")
    op.add_column(
        "papers",
        sa.Column("korea_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.drop_column("papers", "lead_countries_json")
    op.drop_constraint("uq_analysis_year", "analyses", type_="unique")
    op.create_unique_constraint("uq_analysis_year", "analyses", ["subfield_id", "year"])
    op.drop_column("analyses", "country")
```

- [ ] **Step 5: 기존 테스트에서 `korea_flag`를 걷어낸다**

`grep -rn "korea_flag" backend/tests` 로 나오는 곳(약 20군데)에서 해당 인자·단언을
지운다. `test_search.py::test_merge_korea_flag_true_if_any_source_true`는 **테스트 자체를
삭제**한다 — 지운 필드의 병합 규칙이므로 남길 이유가 없다.

- [ ] **Step 6: 테스트를 통과시킨다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 7: 실제 DB 적용을 확인한다**

```bash
cd /home/dev/code/performance-review && docker compose up -d --build api && sleep 20
docker compose exec -T api alembic current
docker compose exec -T db psql -U perfrev -d perfrev -c "\d analyses" | grep country
docker compose exec -T db psql -U perfrev -d perfrev -c "SELECT count(*) FROM analyses WHERE country='KR';"
```
Expected: `0017 (head)` · `country | character varying(2) | not null` · 110

- [ ] **Step 8: 커밋**

```bash
git add backend/alembic/versions/0017_country.py backend/app/models backend/tests
git commit -m "feat(db): 국가 파라미터화 스키마 — analyses.country 외

기존 분석은 country='KR'로 자동 귀속되어 재실행이 필요 없다.
korea_flag는 읽는 곳이 한 군데도 없어 삭제한다."
```

---

### Task 2: OpenAlex 클라이언트 — 국가·정렬·교신저자

**Files:**
- Modify: `backend/app/clients/openalex.py`
- Test: `backend/tests/test_openalex.py`

**Interfaces:**
- Produces: `openalex.count_only(query, year_from, year_to, *, client, country="KR")`,
  `openalex.search(query, year_from, year_to, *, client, limit, country="KR")`.
  `_parse_work`가 `lead_countries: list[str]`를 내고 `korea_flag`를 내지 않는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_openalex.py` 끝에 추가:

```python
def test_filter_expression_uses_given_country():
    expr = openalex._filter_expr("q", 2025, 2025, "CN")
    assert "authorships.institutions.country_code:CN" in expr
    assert ":KR" not in expr


def test_filter_expression_defaults_to_kr():
    assert "country_code:KR" in openalex._filter_expr("q", 2025, 2025)


def test_parse_work_extracts_lead_countries_from_corresponding_authors():
    """참여국(countries)과 주도국(lead_countries)은 다른 값이다 — 실측으로 일본 논문의
    47%가 자국이 주도하지 않은 국제공동연구다."""
    work = {
        "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/x",
        "title": "T", "publication_year": 2026, "cited_by_count": 3,
        "authorships": [
            {"author": {"display_name": "A"}, "is_corresponding": True,
             "institutions": [{"display_name": "KAIST", "country_code": "KR"}]},
            {"author": {"display_name": "B"}, "is_corresponding": False,
             "institutions": [{"display_name": "MIT", "country_code": "US"}]},
        ],
    }
    p = openalex._parse_work(work)
    assert set(p["countries"]) == {"KR", "US"}
    assert p["lead_countries"] == ["KR"]
    assert "korea_flag" not in p


def test_parse_work_leaves_lead_countries_empty_when_no_corresponding_flag():
    """is_corresponding 보유율은 91~94%라 없는 경우가 있다 — 빈 리스트로 두고
    stats가 '주도 미상'으로 센다. 없는 정보를 추측해 채우지 않는다."""
    work = {
        "id": "https://openalex.org/W2", "title": "T", "publication_year": 2026,
        "authorships": [
            {"author": {"display_name": "A"},
             "institutions": [{"display_name": "KAIST", "country_code": "KR"}]},
        ],
    }
    assert openalex._parse_work(work)["lead_countries"] == []
```

`sort` 파라미터가 실제로 실리는지는 기존 `test_openalex.py`가 쓰는
`fake_get_with_retry`(params를 받아 기록)로 확인한다 — 그 헬퍼의 `params`에
`"sort": "cited_by_count:desc"`가 들어 있는지 단언을 추가한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_openalex.py -q`
Expected: FAIL

- [ ] **Step 3: 구현한다**

`_filter_expr`에 국가 파라미터를 넣는다:

```python
def _filter_expr(query: str, year_from: int, year_to: int, country: str = "KR") -> str:
    """연도를 범위로 한 번에 건다 — 연도별 개별 조회 대비 콜수가 1/N이 된다.
    국가 필터를 서버측에 걸어 불필요한 페이지를 받지 않는다."""
    return (
        f"title_and_abstract.search:{_sanitize_query(query)},"
        f"publication_year:{year_from}-{year_to},"
        f"authorships.institutions.country_code:{country}"
    )
```

`_base_params`도 `country`를 받아 넘긴다. `count_only`·`search`의 시그니처에
`country: str = "KR"`을 더하고 `_base_params(query, year_from, year_to, country)`로 부른다.

`search`의 `params`에 정렬을 더한다:

```python
                "sort": "cited_by_count:desc",
```

> 정렬을 항상 거는 이유: 상한(`max_papers_per_analysis`)에 걸려 잘릴 때 **무엇이 남는지**를
> 정하기 위해서다. OpenAlex 기본 정렬(relevance)은 텍스트 유사도가 섞인 불투명한 점수라
> 국가 간 비교의 기준선으로 쓸 수 없다. cursor 페이징과 병용되는 것과 비용이 동일한 것,
> 당해연도도 정렬이 유의미한 것은 실측으로 확인했다.

`_parse_work`에서 `korea_flag`를 지우고 교신저자 소속국을 뽑는다:

```python
    authors, institutions, countries = [], [], []
    lead_countries: list[str] = []
    for a in authorships:
        name = (a.get("author") or {}).get("display_name")
        if name:
            authors.append(name)
        for inst in a.get("institutions") or []:
            if inst.get("display_name"):
                institutions.append(inst["display_name"])
            code = inst.get("country_code")
            if code and code not in countries:
                countries.append(code)
            # is_corresponding이 없는 논문이 6~9% 있다 — 그때는 비워 두고 stats가
            # "주도 미상"으로 센다. 추측해 채우면 주도/참여 비율이 조용히 틀어진다.
            if code and a.get("is_corresponding") and code not in lead_countries:
                lead_countries.append(code)
```

반환 dict에서 `"korea_flag": ...`를 지우고 `"lead_countries": lead_countries,`를 넣는다.

- [ ] **Step 4: 테스트를 통과시킨다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/app/clients/openalex.py backend/tests/test_openalex.py
git commit -m "feat(openalex): 국가 파라미터 · 인용순 정렬 · 교신저자 소속국

정렬을 항상 거는 이유는 상한에 걸려 잘릴 때 무엇이 남는지를 정하기 위해서다.
is_corresponding이 없으면 lead_countries를 비워 둔다 — 추측해 채우면
주도/참여 비율이 조용히 틀어진다."
```

---

### Task 3: search 서비스 — 국가 전달과 KCI 분기

**Files:**
- Modify: `backend/app/services/search.py`
- Modify: `backend/app/clients/kci.py` (`korea_flag` 제거)
- Test: `backend/tests/test_search.py`, `backend/tests/test_kci.py`

**Interfaces:**
- Consumes: Task 2의 `openalex.count_only/search(..., country=)`
- Produces: `search.collect(db, subfield, year_from, year_to, *, client, country="KR")`,
  `search.query_hash(subfield, year_from, year_to, country="KR")`.
  `merge_papers`/`upsert_papers`가 `lead_countries`를 다룬다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_search.py` 끝에 추가:

```python
def test_query_hash_differs_by_country():
    """국가가 해시에 들어가지 않으면 KR 분석과 US 분석이 서로를 최신으로 착각한다."""
    sf = Subfield(id=1, field_id=1, name="s", query="q")
    assert query_hash(sf, 2025, 2025, "KR") != query_hash(sf, 2025, 2025, "US")


def test_query_hash_defaults_to_kr():
    sf = Subfield(id=1, field_id=1, name="s", query="q")
    assert query_hash(sf, 2025, 2025) == query_hash(sf, 2025, 2025, "KR")


def test_merge_takes_longer_lead_countries_list():
    oa = [_paper("10.1/z", lead_countries=["KR"], source="openalex")]
    kci = [_paper("10.1/z", lead_countries=["KR", "US"], source="kci")]
    assert merge_papers(oa, kci)[0]["lead_countries"] == ["KR", "US"]


async def test_collect_skips_kci_for_non_kr(monkeypatch):
    """KCI는 한국학술지 전용이다 — 타국 분석에서 부르면 만료된 키 때문에
    무의미하게 failed되고, 소스가 비대칭이라 KR 논문 수만 부풀린다."""
    called = {"kci": 0}

    async def fake_count(*a, **k):
        return 0, 0.0

    async def fake_search(*a, **k):
        return OpenAlexResult(papers=[], cost_usd=0.0, remaining=None, total_count=0)

    async def fake_kci(*a, **k):
        called["kci"] += 1
        return []

    monkeypatch.setattr(search_module.openalex, "count_only", fake_count)
    monkeypatch.setattr(search_module.openalex, "search", fake_search)
    monkeypatch.setattr(search_module.kci, "search", fake_kci)
    monkeypatch.setattr(settings, "elsevier_api_key", "")

    db, sf = _ctx()
    await search_module.collect(db, sf, 2025, 2025, client=None, country="US")
    assert called["kci"] == 0

    await search_module.collect(db, sf, 2025, 2025, client=None, country="KR")
    assert called["kci"] == 1
```

`_paper` 헬퍼의 기본 dict에서 `korea_flag`를 빼고 `"lead_countries": []`를 넣는다.
`_ctx()`는 이 파일이 이미 쓰는 세션+Subfield 생성 방식을 함수로 뽑아 쓴다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_search.py -q`
Expected: FAIL

- [ ] **Step 3: 구현한다**

`query_hash`에 국가를 넣는다:

```python
def query_hash(subfield: Subfield, year_from: int, year_to: int, country: str = "KR") -> str:
    """검색식·연도 범위·국가가 바뀌면 해시가 달라져 해당 분석이 '갱신 필요'로 표시된다.
    국가를 빼면 KR 분석과 US 분석이 서로를 최신으로 착각한다."""
    raw = f"{subfield.query}\x00{subfield.query_kci or ''}\x00{year_from}-{year_to}\x00{country}"
    return hashlib.sha256(raw.encode()).hexdigest()
```

병합 규칙에 `lead_countries`를 더하고 `korea_flag` 병합 줄을 지운다:

```python
_LONGER_LIST_WINS = ("authors", "institutions", "countries", "lead_countries")
```

`_FIELDS`에서 `korea_flag`를 빼고 `lead_countries`를 넣고, `_JSON_MAP`에
`"lead_countries": "lead_countries_json"`을 더한다.

`collect`에 국가를 넣고 KCI를 분기한다:

```python
async def collect(
    db: Session,
    subfield: Subfield,
    year_from: int,
    year_to: int,
    *,
    client: httpx.AsyncClient,
    country: str = "KR",
) -> SearchResult:
```

본문의 `openalex.count_only(...)`·`openalex.search(...)` 호출에 `country=country`를 넘기고,
KCI 호출을 감싼다:

```python
    # KCI는 한국학술지 전용이다. 타국 분석에서 부르면 (a) 만료된 키 때문에 무의미하게
    # failed되고 (b) KR에만 국내지가 섞여 소스가 비대칭이 된다 — 비교에서 KR 논문 수만
    # 구조적으로 부풀린다.
    kci_papers = []
    if country == "KR":
        kci_papers = await kci.search(
            subfield.kci_query(), year_from, year_to,
            client=client, limit=settings.max_papers_per_analysis,
        )
```

`backend/app/clients/kci.py`에서 `"korea_flag": True,` 줄을 지우고
`"lead_countries": [],`를 넣는다(KCI 응답에는 저자 소속 정보가 없다).

- [ ] **Step 4: 테스트를 통과시킨다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/app/services/search.py backend/app/clients/kci.py backend/tests
git commit -m "feat(search): 국가 파라미터와 KCI 분기

country가 KR이 아니면 KCI를 부르지 않는다 — 만료된 키로 무의미하게
failed되는 것을 막고, KR에만 국내지가 섞이는 소스 비대칭도 없앤다.

query_hash에 국가를 넣는다. 빼면 KR 분석과 US 분석이 서로를 최신으로 착각한다."
```

---

### Task 4: stats — 귀속 4분류와 표본 표기

**Files:**
- Modify: `backend/app/services/stats.py`
- Test: `backend/tests/test_stats.py`

**Interfaces:**
- Produces: `stats.compute(papers, extractions, *, snapshot_at, country="KR", population_total=None)`.
  반환에 `attribution`(dict), `population_total`(int), `sampled`(bool) 추가.
  `intl_collab_ratio`·`top_partner_countries`의 `"KR"` 하드코딩 제거.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_attribution_splits_solo_lead_participant_unknown():
    """참여 기준만 쓰면 JP와 CN의 숫자를 같은 의미로 읽게 된다 — 실측으로 일본 논문의
    47%가 자국이 주도하지 않은 국제공동연구이고 중국은 7.5%뿐이다."""
    papers = [
        _p("solo", countries_json=["KR"], lead_countries_json=["KR"]),
        _p("lead", countries_json=["KR", "US"], lead_countries_json=["KR"]),
        _p("part", countries_json=["KR", "US"], lead_countries_json=["US"]),
        _p("unk",  countries_json=["KR", "US"], lead_countries_json=[]),
    ]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 8, 2), country="KR")
    assert s["attribution"] == {"단독": 1, "주도": 1, "참여": 1, "주도 미상": 1}


def test_attribution_follows_the_analysis_country():
    papers = [_p("a", countries_json=["US", "KR"], lead_countries_json=["US"])]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 8, 2), country="US")
    assert s["attribution"]["주도"] == 1


def test_partner_countries_exclude_the_analysis_country():
    papers = [_p("a", countries_json=["CN", "US"], lead_countries_json=["CN"])]
    s = stats.compute(papers, [], snapshot_at=datetime(2026, 8, 2), country="CN")
    assert dict(s["top_partner_countries"]) == {"US": 1}
    assert s["intl_collab_ratio"] == 1.0


def test_sampled_flag_marks_truncated_collections():
    """상한에 걸려 잘린 표본을 전수와 나란히 놓으면 인용수가 구조적으로 부풀려진다 —
    표본임을 반드시 드러낸다."""
    papers = [_p("a")]
    full = stats.compute(papers, [], snapshot_at=datetime(2026, 8, 2), population_total=1)
    cut = stats.compute(papers, [], snapshot_at=datetime(2026, 8, 2), population_total=5000)
    assert full["sampled"] is False and full["population_total"] == 1
    assert cut["sampled"] is True and cut["population_total"] == 5000
```

`_p` 헬퍼에서 `korea_flag`를 빼고 `lead_countries_json=[]`를 기본값으로 넣는다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_stats.py -k "attribution or partner or sampled" -q`
Expected: FAIL

- [ ] **Step 3: 구현한다**

`compute` 시그니처와 집계 루프를 고친다:

```python
def compute(
    papers: list[Paper],
    extractions: list[PaperExtraction],
    *,
    snapshot_at: datetime,
    country: str = "KR",
    population_total: int | None = None,
) -> dict:
```

루프에서 `"KR"` 하드코딩을 `country`로 바꾸고 귀속을 함께 센다:

```python
    citations = [p.citations or 0 for p in papers]
    partner_counter: Counter = Counter()
    attribution: Counter = Counter()
    intl = 0
    with_country = 0
    for p in papers:
        countries = p.countries_json or []
        if not countries:
            continue
        with_country += 1
        others = [c for c in countries if c != country]
        if others:
            intl += 1
            partner_counter.update(others)

        # 참여 기준으로 수집하되 주도 여부를 병기한다. 둘을 구분하지 않으면 국가별
        # 숫자를 같은 의미로 오독한다(실측: JP는 47%가 참여만, CN은 7.5%).
        leads = p.lead_countries_json or []
        if not others:
            attribution["단독"] += 1
        elif not leads:
            attribution["주도 미상"] += 1   # is_corresponding 미보유 6~9%
        elif country in leads:
            attribution["주도"] += 1
        else:
            attribution["참여"] += 1
```

반환 dict에 세 키를 더한다(`by_achievement_type` 아래):

```python
        "attribution": dict(attribution),
        # 상한에 걸려 잘렸는지. 표본과 전수를 나란히 놓으면 인용수가 구조적으로
        # 부풀려지므로 반드시 드러낸다(비교 보고서가 이 값을 읽어 경고한다).
        "population_total": population_total if population_total is not None else len(papers),
        "sampled": bool(population_total is not None and population_total > len(papers)),
```

- [ ] **Step 4: 테스트를 통과시킨다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/app/services/stats.py backend/tests/test_stats.py
git commit -m "feat(stats): 귀속 4분류와 표본 표기

단독/주도/참여/주도 미상으로 나눈다 — 참여 기준만 쓰면 JP(참여만 47%)와
CN(7.5%)의 숫자를 같은 의미로 읽게 된다.

population_total·sampled로 상한 절단을 드러낸다. 표본과 전수를 나란히
놓으면 인용수가 구조적으로 부풀려진다."
```

---

### Task 5: runner — 국가별 큐잉과 하드 가드 제거

**Files:**
- Modify: `backend/app/services/runner.py`
- Test: `backend/tests/test_runner.py`, `backend/tests/test_scheduler.py`

**Interfaces:**
- Consumes: Task 1~4 전부
- Produces: `runner.enqueue(db, subfield, year_from, year_to, *, force, trigger="manual", country="KR")`.
  `AnalysisTooLarge` **삭제**.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
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
    거부 대신 인용 상위 N건을 수집하고 표본임을 기록한다."""
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


def test_analysis_too_large_is_gone():
    assert not hasattr(runner, "AnalysisTooLarge")
```

`test_scheduler.py`에는 국가 순회 테스트를 더한다:

```python
def test_scheduler_queues_every_configured_country(ctx, monkeypatch):
    """schedule_settings.countries가 콤마 구분 목록이다. 기본 KR이라 켜기 전에는
    현행과 같다."""
    db, sf = ctx
    cfg = runner.get_schedule_settings(db)
    cfg.countries = "KR,US"
    cfg.years_back = 0
    db.commit()

    runner.run_scheduled_now(db, now=datetime(2026, 8, 2, 3, 0))
    rows = db.query(Analysis).all()
    assert {a.country for a in rows} == {"KR", "US"}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_runner.py tests/test_scheduler.py -q`
Expected: FAIL

- [ ] **Step 3: 구현한다**

`AnalysisTooLarge` 클래스 정의와 `_do_search`의 그 블록을 **삭제**하고, 대신
표본 여부를 넘긴다. `_do_search`:

```python
async def _do_search(db: Session, analysis: Analysis, subfield: Subfield) -> None:
    async with httpx.AsyncClient() as client:
        result = await search.collect(
            db, subfield, analysis.year, analysis.year,
            client=client, country=analysis.country,
        )

    # 상한을 넘으면 거부하지 않고 인용 상위 N건을 수집한다(openalex.search가
    # sort=cited_by_count:desc로 받는다). 거부하면 CN 11개·US 3개 세부기술이 그냥
    # 실패한다(실측). 잘렸다는 사실은 stats의 population_total·sampled가 드러낸다.
    #
    # total_count는 _do_reduce 시점에는 다시 얻을 수 없으므로(그때는 DB의 논문
    # 링크만 본다) 여기서 stats_json에 실어 둔다. 아래 _do_reduce가 읽어 간다.
    analysis.stats_json = {
        **(analysis.stats_json or {}),
        "population_total": result.total_count,
    }
```

이 대입은 `rows = search.upsert_papers(...)` **앞**에 둔다(같은 `db.commit()`에 실린다).

`_do_reduce`의 `stats.compute` 호출:

```python
    analysis.stats_json = stats.compute(
        papers, extractions,
        snapshot_at=analysis.snapshot_at or datetime.now(timezone.utc),
        country=analysis.country,
        population_total=(analysis.stats_json or {}).get("population_total"),
    )
```

`enqueue`에 국가를 넣는다 — 조회·생성·해시 세 군데 전부:

```python
def enqueue(
    db: Session, subfield: Subfield, year_from: int, year_to: int, *, force: bool,
    trigger: str = "manual", country: str = "KR",
) -> list[Analysis]:
    ...
    for year in range(year_from, year_to + 1):
        current_hash = search.query_hash(subfield, year, year, country)
        row = db.query(Analysis).filter(
            Analysis.subfield_id == subfield.id,
            Analysis.year == year,
            Analysis.country == country,
        ).first()

        if row is None:
            row = Analysis(subfield_id=subfield.id, year=year, status="pending",
                           query_hash=current_hash, trigger=trigger,
                           extracted_this_run=0, country=country)
```

`_queue_all_active`가 국가를 순회한다:

```python
    years = [now.year - i for i in range(cfg.years_back + 1)]
    countries = [c.strip() for c in (cfg.countries or "KR").split(",") if c.strip()]
    subfields = db.query(Subfield).filter(Subfield.active.is_(True)).all()
    queued = 0
    for subfield in subfields:
        for year in years:
            for country in countries:
                queued += len(enqueue(db, subfield, year, year, force=True,
                                      trigger=trigger, country=country))
```

`advance`의 `except AnalysisTooLarge` 블록이 있으면 함께 지운다.

- [ ] **Step 4: 테스트를 통과시킨다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/app/services/runner.py backend/tests
git commit -m "feat(runner): 국가별 큐잉과 상한 거부 제거

AnalysisTooLarge를 삭제한다 — 상한 초과를 거부하면 CN 11개·US 3개
세부기술이 그냥 실패한다(실측). 인용 상위 N건을 수집하고 표본임을
stats의 population_total·sampled로 드러낸다.

스케줄러가 schedule_settings.countries를 순회한다. 기본 KR이라 켜기
전에는 현행과 같다."
```

---

### Task 6: 프롬프트 국가 표기

**Files:**
- Modify: `backend/app/prompts.py`, `backend/app/services/reducer.py`
- Test: `backend/tests/test_reducer.py`

**Interfaces:**
- Consumes: `Analysis.country`
- Produces: `reduce_subfield`의 입력 헤더가 `[세부기술: 이름 / 연도 / 국가명]`.

`EXTRACTION_SCHEMA_VERSION`은 **올리지 않는다** — `model_ver`가 프롬프트 텍스트를 보지 않아
캐시가 유지된다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
COUNTRY_NAMES_EXPECTED = {"KR": "한국", "US": "미국", "CN": "중국", "JP": "일본", "DE": "독일"}


def test_country_name_lookup_covers_target_countries():
    from app.prompts import country_name
    for code, name in COUNTRY_NAMES_EXPECTED.items():
        assert country_name(code) == name
    assert country_name("XX") == "XX"   # 모르는 코드는 그대로 — 지어내지 않는다


async def test_reduce_header_names_the_country(monkeypatch):
    """국가가 빠지면 KR 보고서와 CN 보고서의 H1 제목이 같아져 구분이 불가능해진다."""
    fake = _FakeGenerate()
    monkeypatch.setattr(reducer.gemini_sync, "generate", fake)
    ext = [_ext("k1", "공정")]
    papers = {"k1": Paper(paper_key="k1", title="논문", year=2026, journal="J",
                          abstract="A", source="openalex", citations=1)}
    analysis = Analysis(subfield_id=1, year=2026, query_hash="h", country="CN")

    await reducer.reduce_subfield(_FakeDb(), analysis, ext, papers)

    assert "[세부기술: 테스트 세부기술 / 2026 / 중국]" in fake.calls[0][1]


def test_map_instruction_is_country_neutral():
    from app.prompts import MAP_INSTRUCTION
    assert "한국" not in MAP_INSTRUCTION
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_reducer.py -k "country or map_instruction" -q`
Expected: FAIL

- [ ] **Step 3: 구현한다**

`backend/app/prompts.py` 상단에 추가:

```python
# 보고서 본문에 쓸 국가명. 모르는 코드는 그대로 돌려준다 — 지어내면 보고서에
# 없는 나라 이름이 박힌다.
COUNTRY_NAMES = {
    "KR": "한국", "US": "미국", "CN": "중국", "JP": "일본", "DE": "독일",
    "GB": "영국", "FR": "프랑스", "TW": "대만", "IN": "인도", "CA": "캐나다",
}


def country_name(code: str) -> str:
    return COUNTRY_NAMES.get(code, code)
```

`MAP_INSTRUCTION` 첫 줄에서 "한국"을 뺀다:

```python
MAP_INSTRUCTION = """당신은 연구성과를 분석하는 과학기술 분석가입니다.
```

`REDUCE_INSTRUCTION`의 "한국 논문들에서 추출한"을 국가 중립으로 바꾼다:

```
아래는 특정 국가의 특정 세부기술 분야 논문들에서 추출한 기술적 성과 목록입니다.
```

같은 지시문의 헤더 설명도 고친다:

```
입력 첫 줄의 `[세부기술: 이름 / 연도 / 국가]`가 이 보고서의 대상입니다. 그 국가의
그 세부기술 성과만 다루세요 — 내용을 보고 분야명을 새로 지어내거나 다른 분야
이야기로 넘어가지 마세요.
```

`backend/app/services/reducer.py`의 헤더 조립:

```python
    subfield = db.get(Subfield, analysis.subfield_id)
    header = (
        f"[세부기술: {subfield.name if subfield else '미상'} / {analysis.year}"
        f" / {country_name(analysis.country)}]\n"
    )
```

`from app.prompts import ... country_name`을 import에 더한다.

- [ ] **Step 4: 테스트를 통과시킨다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: 커밋 후 PR A를 연다**

```bash
git add backend/app/prompts.py backend/app/services/reducer.py backend/tests/test_reducer.py
git commit -m "feat(prompts): 국가 표기

reduce 입력 헤더에 국가를 넣는다 — 빼면 KR 보고서와 CN 보고서의 H1 제목이
같아져 구분이 불가능해진다.

MAP_INSTRUCTION에서 '한국'을 뺐지만 EXTRACTION_SCHEMA_VERSION은 올리지
않는다. model_ver는 프롬프트 텍스트를 보지 않아 캐시가 유지된다."
```

**여기까지가 PR A다.** 기본값 `KR` 덕분에 이것만 머지해도 동작이 그대로여야 한다.
배포 후 기존 분석 하나를 열어 화면이 이전과 같은지 확인한다.

---

### Task 7: API — 국가 파라미터

**Files:**
- Modify: `backend/app/routers/public.py`, `backend/app/routers/admin.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Produces: `GET /api/subfields/{sid}/analyses/{year}?country=US`(기본 `KR`),
  `_serialize`가 `country`·`country_name`을 내려준다.
  `POST /api/admin/run`이 `country` 필드를 받는다(기본 `KR`).
  `GET/PUT /api/admin/schedule`이 `countries`를 주고받는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
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
                  query_hash="h2", report_md="미국 보고서", country="US")
    db.add(us)
    db.commit()

    got = client.get(f"/api/subfields/{kr.subfield_id}/analyses/{kr.year}?country=US").json()
    assert got["country"] == "US"
    assert "미국 보고서" in got["report_md"]


def test_admin_schedule_roundtrips_countries(client, admin_headers):
    client.put("/api/admin/schedule",
               json={"enabled": True, "day": 10, "hour": 3, "years_back": 1,
                     "countries": "KR,US"}, headers=admin_headers)
    assert client.get("/api/admin/schedule", headers=admin_headers).json()["countries"] == "KR,US"
```

`admin_headers`는 이 파일이 이미 쓰는 관리자 인증 헬퍼를 그대로 쓴다(파일 상단 확인).

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_api.py -k country -q`
Expected: FAIL

- [ ] **Step 3: 구현한다**

`public.py`의 `get_by_subfield_year`에 쿼리 파라미터를 더하고 필터에 넣는다:

```python
@router.get("/subfields/{subfield_id}/analyses/{year}")
def get_by_subfield_year(
    subfield_id: int, year: int, country: str = "KR", db: Session = Depends(get_db)
):
```

`_serialize`의 반환 dict에 두 줄을 더한다:

```python
        "country": analysis.country,
        "country_name": country_name(analysis.country),
```

`years` 조회에도 국가 조건을 넣는다 — 다른 국가의 연도가 섞이면 이동 링크가 404로 간다:

```python
        .filter(Analysis.subfield_id == subfield.id, Analysis.country == analysis.country)
```

`admin.py`의 실행 요청 모델과 스케줄 모델에 필드를 더한다(해당 Pydantic 모델에
`country: str = "KR"` / `countries: str = "KR"`), 그리고 `enqueue`·`ScheduleSetting`에 전달한다.

- [ ] **Step 4: 테스트를 통과시킨다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/app/routers backend/tests/test_api.py
git commit -m "feat(api): 국가 파라미터

조회는 ?country=(기본 KR), 실행 요청과 스케줄 설정에도 국가를 받는다.
연도 목록도 같은 국가로 좁힌다 — 섞이면 이동 링크가 404로 간다."
```

---

### Task 8: 화면 — 국가 선택

**Files:**
- Modify: `frontend/src/api.ts`, `frontend/src/pages/Report.tsx`,
  `frontend/src/components/ScheduleSection.tsx`, `frontend/package.json`

**Interfaces:**
- Consumes: Task 7의 `country`·`country_name`, `?country=` 쿼리

- [ ] **Step 1: 타입을 더한다**

`frontend/src/api.ts`의 `Analysis` 인터페이스에:

```ts
  country: string;
  country_name: string;
```

`ScheduleSettings`(또는 그에 해당하는 타입)에 `countries: string;`을 더한다.

- [ ] **Step 2: 보고서 화면에 국가를 표시한다**

`Report.tsx`의 제목 부근(세부기술명 옆)에 국가를 붙인다. 기존 `StatusBadge` 옆
메타 정보와 같은 줄에 두고, 클래스는 그 줄이 이미 쓰는 것을 따른다:

```tsx
<span className="text-sm text-muted">{data.country_name}</span>
```

`useSearchParams`로 `?country=`를 읽어 조회에 넘긴다(기존 `withSections` 토글이 이미
`useSearchParams`를 쓰므로 같은 훅을 재사용한다).

- [ ] **Step 3: 관리자 스케줄 화면에 국가 입력을 더한다**

`ScheduleSection.tsx`에 `countries` 텍스트 입력을 더한다. `.input` 클래스를 쓰고,
설명 문구로 "콤마 구분(예: KR,US,CN). 국가마다 검색·추출이 따로 돌아 비용이 곱해집니다"를 붙인다.

- [ ] **Step 4: 버전을 올리고 검증한다**

`frontend/package.json`의 `version`을 minor 하나 올린다.

```bash
cd frontend && npm run build && npm run lint && npm test
```
Expected: 셋 다 PASS

- [ ] **Step 5: 커밋 후 PR B를 연다**

```bash
git add frontend
git commit -m "feat(frontend): 국가 표시와 스케줄 국가 설정"
```

---

## 완료 조건

- `cd backend && ./.venv/bin/python -m pytest` 전체 통과
- `cd frontend && npm run build && npm run lint && npm test` 전부 통과
- `docker compose exec -T api alembic current` → `0017 (head)`
- **기존 110건이 전부 `country='KR'`이고 화면이 이전과 동일하다**

## 검증 (배포 후)

US 또는 CN 세부기술 **1개**를 종단 실행해 확인한다. 차세대 메모리반도체 CN(2025년 821건)이
적당하다 — 5,000건 상한 아래라 표본 절단 없이 전 구간을 볼 수 있고, KR(304건)과 비교도 된다.

1. `country='CN'` 분석 행이 KR과 별개로 생기는가
2. KCI를 부르지 않는가(로그)
3. `stats_json`에 `attribution`·`population_total`·`sampled`가 들어가는가
4. 보고서 H1 제목에 국가가 반영되는가
5. **기존 KR 분석이 재실행 없이 그대로 보이는가**

그다음 **CN 재생에너지(25,466건)**로 상한 절단 경로를 확인한다 — `sampled=true`가 되고
`population_total`이 25,466으로 남아야 한다. 이건 비용이 크므로(map 약 $1.4) 별도 판단.

## 이 계획이 하지 않는 것

- **비교 보고서** — 5단계(`country_comparisons`, `COMPARE_INSTRUCTION`).
- **`EXTRACTION_SCHEMA_VERSION` 상향** — 프롬프트 변경은 캐시를 무효화하지 않는다.
- **자국어 검색식(중문·일문)** — OpenAlex 색인이 영문 중심이라 번역해도 잡히지 않는다.
- **국가별 예산 분리** — 현행 단일 일일 예산 + `paused` 재개로 충분하다.
