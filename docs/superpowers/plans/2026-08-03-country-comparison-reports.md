# 국가 비교 보고서 (5단계) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 같은 세부기술·연도의 국가별 분석을 하나의 비교 보고서로 합성한다.

**Architecture:** `FieldReport`와 동일한 큐잉 패턴(`enqueue_*` 검증 → `pending` 행 → 잡 루프가
한 틱에 하나씩 `process_*`). 숫자 대조표는 **코드가** `stats_json`에서 조립하고 LLM은 인용만
한다. 새 테이블 `country_comparisons`, 새 프롬프트 `COMPARE_INSTRUCTION`, 마이그레이션 0018.

**Tech Stack:** FastAPI · SQLAlchemy · Alembic · pytest / React 19 · Vite · Tailwind · vitest

## Global Constraints

- 마이그레이션 head는 현재 **0017**. 새 리비전은 `down_revision = "0017"`.
- 새 모델은 **`app/models/__init__.py`에 반드시 추가**한다(FK 해석·autogenerate가 의존).
- 백엔드 테스트는 `backend/.venv`로만 돈다: `cd backend && ./.venv/bin/python -m pytest`.
- 통계·숫자는 **코드로만** 만든다. LLM에 계산을 맡기지 않는다(`stats.py` 원칙).
- 프론트 간격은 4/8/12/16/24/40(Tailwind `1·2·3·4·6·10`)만. `src/lib/spacing.test.ts`가 강제.
- 넓은 표는 `.table-scroll`로 감싼다(`overflow-x-auto` 단독 금지).
- 프론트를 고치면 `frontend/package.json`의 `version`을 함께 올린다(기능 추가 minor).
- `EXTRACTION_SCHEMA_VERSION`은 **건드리지 않는다**(전량 재추출 유발).

---

## 설계 근거 — 계획 전에 확인한 것

### 왜 세부기술 단위인가

입력이 이미 완성된 국가별 보고서 2~5건이라 **LLM 1콜**로 끝난다. 분야 단위는
국가 × 세부기술이라 컨텍스트가 크고 어디서 벌어진 차이인지 추적이 어렵다.

### 입력에 세부 보고서(`sections_json`)를 넣지 않는다

실측(2026-08-03):

| | 종합 `report_md` | 세부 `sections_json` |
|---|---:|---:|
| 차세대 메모리반도체 CN 2025 (731건 분석) | 4,813자 | **144,730자** |
| 차세대 메모리반도체 KR 2025 (245건 분석) | 10,549자 | 없음 |

세부까지 넣으면 5개국에서 725KB(약 18만 토큰)가 되고, **2단계에서 확인한 이중 압축
문제를 비교 단계에서 반복**한다. 종합만 넣는다.

**대신 그 대가를 프롬프트가 알아야 한다.** 위 표에서 보듯 논문이 2.98배 많은 CN의 종합이
KR의 절반이다 — 3단 reduce가 9개 그룹으로 나눠 압축했기 때문이고, 길이는 말뭉치 크기에
따른 **압축률 차이**이지 내용의 깊이가 아니다. 이를 금지 조항으로 못박지 않으면 비교
보고서가 "중국은 연구 내용이 빈약하다"는 정반대 결론을 낸다(Task 3).

현재 3단 reduce에 걸린 분석은 111건 중 2건뿐이라 이 문제는 아직 드물지만, 전체
재실행(2026-08-03 진행 중)으로 논문이 약 13% 늘면 500건 경계를 넘는 분석이 늘어난다.

### `_process_report`는 그대로 못 쓴다

`runner.py:640` `_process_report`가 `row.field_id`를 로그에 직접 참조한다.
비교 행은 `subfield_id`를 가지므로 **AttributeError**가 난다. Task 5에서 로그를
행 종류와 무관하게 만든다.

### 참여 기준 중복 계상

`attribution`은 참여 기준이라 **국가별 합계가 총합과 다르다**(공동연구가 양쪽에 계상).
대조표에 이를 각주로 명시하고 프롬프트가 합산을 금지한다.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `backend/app/models/field.py` (수정) | `CountryComparison` 모델 추가 |
| `backend/app/models/__init__.py` (수정) | 새 모델 import |
| `backend/alembic/versions/0018_country_comparison.py` (생성) | 테이블 생성 |
| `backend/app/services/comparison.py` (생성) | 대조표 조립 + 큐잉 + 처리 (신규 서비스) |
| `backend/app/prompts.py` (수정) | `COMPARE_INSTRUCTION` |
| `backend/app/services/runner.py` (수정) | 잡 루프에 비교 처리 추가, `_process_report` 로그 일반화 |
| `backend/app/routers/admin.py` (수정) | 큐잉 엔드포인트 + 현황 |
| `backend/app/routers/public.py` (수정) | 조회 엔드포인트 |
| `backend/tests/test_comparison.py` (생성) | 대조표·큐잉·처리 테스트 |
| `frontend/src/pages/ComparisonPage.tsx` (생성) | 비교 보고서 전용 페이지 |
| `frontend/src/App.tsx` (수정) | 라우트 추가 |
| `frontend/src/api.ts` (수정) | 조회·큐잉 함수 (경로 주의: `src/lib/` 아님) |
| `frontend/src/pages/Admin.tsx` (수정) | 비교 생성 버튼 |

**비교 로직을 `reducer.py`가 아니라 새 `comparison.py`에 두는 이유**: `reducer.py`는 이미
650줄이고 세부기술 reduce·분야 rollup·로드맵 점검 세 가지를 담고 있다. 비교는 입력 조립
로직(대조표)이 따로 있어 독립 파일이 맞다.

---

## Task 1: `CountryComparison` 모델 + 마이그레이션 0018

**Files:**
- Modify: `backend/app/models/field.py` (파일 끝에 추가)
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/0018_country_comparison.py`
- Test: `backend/tests/test_comparison.py` (신규)

**Interfaces:**
- Produces: `CountryComparison(id, subfield_id, year, countries, status, error, report_md, generated_at, source_count)`
  - `countries`: 콤마 구분 국가 코드 문자열(예: `"KR,US,CN"`) — 정렬해 저장한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_comparison.py`:

```python
"""국가 비교 보고서 — 모델·대조표·큐잉·처리."""

from datetime import datetime

from app.models import CountryComparison


def test_country_comparison_roundtrip(db_session):
    """국가 목록은 정렬된 콤마 문자열로 저장된다."""
    row = CountryComparison(
        subfield_id=1,
        year=2026,
        countries="CN,KR,US",
        generated_at=datetime(2026, 8, 3),
    )
    db_session.add(row)
    db_session.commit()

    saved = db_session.query(CountryComparison).one()
    assert saved.countries == "CN,KR,US"
    # FieldReport와 같은 기본값 — 생성 전에는 빈 본문
    assert saved.status == "done"
    assert saved.report_md == ""
    assert saved.source_count == 0
```

`conftest.py`에 `db_session` 픽스처가 없으면 `test_models.py`가 쓰는 방식을 그대로 따른다
(먼저 `backend/tests/conftest.py`와 `test_models.py`를 읽어 확인할 것).

- [ ] **Step 2: 실패 확인**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_comparison.py -v`
Expected: FAIL — `ImportError: cannot import name 'CountryComparison'`

- [ ] **Step 3: 모델 추가**

`backend/app/models/field.py` 끝에:

```python
class CountryComparison(Base):
    """같은 세부기술·연도의 국가별 분석을 합성한 비교 보고서 캐시.

    FieldReport와 같은 큐잉 패턴이다 — 관리자가 누르면 pending 행만 만들고
    실제 LLM 호출은 runner.loop이 한 틱에 하나씩 처리한다.

    countries가 키에 포함되는 이유: 같은 세부기술·연도라도 "KR,US"와 "KR,US,CN"은
    다른 보고서다. 국가 조합을 바꿔 다시 만들어도 기존 것을 덮어쓰지 않는다.
    """

    __tablename__ = "country_comparisons"
    __table_args__ = (
        UniqueConstraint("subfield_id", "year", "countries", name="uq_comparison"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subfield_id: Mapped[int] = mapped_column(ForeignKey("subfields.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    # 정렬된 콤마 구분 국가 코드("CN,KR,US"). 목록을 정렬해 저장하는 이유는
    # 같은 조합을 다른 순서로 요청해도 같은 행을 재사용하기 위해서다.
    countries: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="done")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FieldReport와 같은 이유로 재생성 중에도 옛 본문을 남긴다.
    report_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 합성에 실제로 들어간 국가 수.
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

`backend/app/models/__init__.py`의 `app.models.field` import에 `CountryComparison`을 추가:

```python
from app.models.field import (  # noqa: F401
    CountryComparison,
    Field,
    FieldReport,
    Roadmap,
    RoadmapCheck,
    Subfield,
)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_comparison.py -v`
Expected: PASS

- [ ] **Step 5: 마이그레이션 작성**

`backend/alembic/versions/0018_country_comparison.py`:

```python
"""country_comparisons 테이블

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "country_comparisons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subfield_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("countries", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="done"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("report_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["subfield_id"], ["subfields.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subfield_id", "year", "countries", name="uq_comparison"),
    )


def downgrade() -> None:
    op.drop_table("country_comparisons")
```

- [ ] **Step 6: 마이그레이션 검증**

컨테이너는 **메인 체크아웃**에서 빌드되므로 워크트리의 마이그레이션은 `docker compose up`으로
확인되지 않는다. 임시 DB로 전 구간을 돌린다:

```bash
cd /home/dev/code/performance-review
docker compose exec -T db psql -U perfrev -d postgres -c "DROP DATABASE IF EXISTS scratch0018;"
docker compose exec -T db psql -U perfrev -d postgres -c "CREATE DATABASE scratch0018;"
docker compose exec -T -e DATABASE_URL=postgresql+psycopg://perfrev:perfrev@db:5432/scratch0018 \
  api alembic upgrade head
docker compose exec -T -e DATABASE_URL=postgresql+psycopg://perfrev:perfrev@db:5432/scratch0018 \
  api alembic downgrade 0017
docker compose exec -T -e DATABASE_URL=postgresql+psycopg://perfrev:perfrev@db:5432/scratch0018 \
  api alembic upgrade head
docker compose exec -T db psql -U perfrev -d scratch0018 -c "\d country_comparisons"
docker compose exec -T db psql -U perfrev -d postgres -c "DROP DATABASE scratch0018;"
```

Expected: 0001→0018 적용 성공, 0018→0017 다운그레이드 시 테이블 삭제, 재적용 성공.

> 워크트리 코드로 컨테이너를 돌리려면 위 명령이 **메인 체크아웃 기준**임에 주의.
> 워크트리의 마이그레이션 파일을 컨테이너에 복사하거나
> (`docker compose cp backend/alembic/versions/0018_*.py api:/app/alembic/versions/`),
> 머지 후 검증한다. 복사했으면 검증 뒤 컨테이너를 재생성해 원상복구할 것.

- [ ] **Step 7: 전체 테스트 + 커밋**

```bash
cd backend && ./.venv/bin/python -m pytest
git add backend/app/models/ backend/alembic/versions/0018_country_comparison.py backend/tests/test_comparison.py
git commit -m "feat: CountryComparison 모델 + 마이그레이션 0018"
```

---

## Task 2: 대조표 조립 — 숫자는 코드가 만든다

**Files:**
- Create: `backend/app/services/comparison.py`
- Test: `backend/tests/test_comparison.py` (추가)

**Interfaces:**
- Consumes: `Analysis.stats_json`(Task 없음 — 기존), `prompts.country_name`
- Produces:
  - `collect_country_analyses(db, subfield_id, year, countries) -> list[tuple[str, Analysis]]`
  - `build_comparison_table(rows) -> str` — 마크다운 표 문자열

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_comparison.py`에 추가:

```python
import json

from app.services import comparison


def _stats(**over):
    base = {
        "searched_count": 300,
        "analyzed_count": 245,
        "population_total": 300,
        "sampled": False,
        "no_abstract_count": 55,
        "attribution": {"단독": 200, "주도": 30, "참여": 10, "주도 미상": 5},
        "citations": {"median": 2, "p90": 12, "total": 900},
        "by_achievement_type": {"신소자": 100, "공정": 45},
        "intl_collab_ratio": 0.18,
    }
    base.update(over)
    return base


def test_comparison_table_has_a_column_per_country():
    rows = [
        ("KR", _stats()),
        ("CN", _stats(searched_count=820, analyzed_count=731,
                      population_total=821, sampled=True)),
    ]
    table = comparison.build_comparison_table(rows)

    # 국가명이 열 머리로 들어간다
    assert "한국" in table and "중국" in table
    # 핵심 행이 전부 있다
    for label in ("모집단", "수집", "표본율", "분석", "단독", "주도", "참여"):
        assert label in table
    # 숫자가 그대로 실린다(LLM이 계산하지 않는다)
    assert "731" in table and "245" in table


def test_comparison_table_marks_sampled_country():
    """표본인 국가는 표본율이 100% 미만으로 드러나야 한다 —
    이 행이 없으면 프롬프트가 인용수 비교 금지를 판단할 근거를 잃는다."""
    rows = [
        ("KR", _stats(population_total=300, searched_count=300, sampled=False)),
        ("US", _stats(population_total=7785, searched_count=5000, sampled=True)),
    ]
    table = comparison.build_comparison_table(rows)

    assert "100%" in table   # KR
    assert "64%" in table    # US: 5000/7785
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_comparison.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.comparison'`

- [ ] **Step 3: 구현**

`backend/app/services/comparison.py`:

```python
"""국가 비교 보고서 — 대조표 조립 + 큐잉 + 처리.

숫자 대조는 전부 여기(코드)서 만들고 LLM은 그 표를 인용만 한다.
stats.py의 원칙("숫자를 LLM에 맡기면 틀린다")이 비교에서 특히 중요하다 —
비교 보고서는 숫자 대조가 본체이기 때문이다.
"""

from __future__ import annotations

import logging

from app.prompts import country_name

logger = logging.getLogger(__name__)


def _pct(part: int, whole: int) -> str:
    """0으로 나누는 경우는 '—'. 모집단이 0인 국가가 섞일 수 있다."""
    if not whole:
        return "—"
    return f"{round(part / whole * 100)}%"


def build_comparison_table(rows: list[tuple[str, dict]]) -> str:
    """국가별 stats_json에서 대조표(마크다운)를 만든다.

    LLM은 이 표를 그대로 인용만 하고 다시 계산하지 않는다. 표본율 행이 특히
    중요하다 — 이것이 없으면 프롬프트가 "인용수를 비교하지 말라"를 판단할
    근거를 잃는다.
    """
    names = [country_name(code) for code, _ in rows]
    header = "| 항목 | " + " | ".join(names) + " |"
    sep = "|---|" + "---:|" * len(rows)

    def line(label: str, fn) -> str:
        return f"| {label} | " + " | ".join(str(fn(s)) for _, s in rows) + " |"

    body = [
        line("모집단(전체)", lambda s: f"{s.get('population_total') or s.get('searched_count', 0):,}"),
        line("수집", lambda s: f"{s.get('searched_count', 0):,}"),
        line("표본율", lambda s: _pct(
            s.get("searched_count", 0),
            s.get("population_total") or s.get("searched_count", 0),
        )),
        line("분석(abstract 보유)", lambda s: f"{s.get('analyzed_count', 0):,}"),
        line("abstract 미보유", lambda s: f"{s.get('no_abstract_count', 0):,}"),
        line("단독", lambda s: f"{s.get('attribution', {}).get('단독', 0):,}"),
        line("주도", lambda s: f"{s.get('attribution', {}).get('주도', 0):,}"),
        line("참여", lambda s: f"{s.get('attribution', {}).get('참여', 0):,}"),
        line("주도 미상", lambda s: f"{s.get('attribution', {}).get('주도 미상', 0):,}"),
        line("국제공동 비율", lambda s: _pct(
            round(s.get("intl_collab_ratio", 0) * 1000), 1000
        )),
        line("인용 중앙값", lambda s: s.get("citations", {}).get("median", 0)),
        line("인용 p90", lambda s: s.get("citations", {}).get("p90", 0)),
    ]
    return "\n".join([header, sep, *body])
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_comparison.py -v`
Expected: PASS

- [ ] **Step 5: 성과유형 분포 행 추가 (실패 테스트 먼저)**

성과유형은 국가마다 키가 다르므로 위 `line()`으로는 안 된다. 별도 처리한다.

테스트 추가:

```python
def test_comparison_table_includes_achievement_types():
    """성과유형은 국가마다 키가 다르므로 합집합을 만들어 0으로 채운다 —
    빠뜨리면 '그 국가엔 그 유형이 없다'와 '집계에서 누락됐다'가 구별되지 않는다."""
    rows = [
        ("KR", _stats(by_achievement_type={"신소자": 100, "공정": 45})),
        ("CN", _stats(by_achievement_type={"신소자": 96, "아키텍처": 300})),
    ]
    table = comparison.build_comparison_table(rows)

    assert "아키텍처" in table and "공정" in table
    # KR엔 아키텍처가 없으므로 0
    lines = [l for l in table.splitlines() if l.startswith("| 아키텍처")]
    assert len(lines) == 1
    assert "| 0 |" in lines[0]
```

- [ ] **Step 6: 실패 확인 후 구현**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_comparison.py::test_comparison_table_includes_achievement_types -v`
Expected: FAIL

`build_comparison_table`의 `body` 뒤에 추가:

```python
    # 성과유형은 국가마다 키가 달라 합집합을 만들고 없는 곳은 0으로 채운다.
    # 빠뜨리면 "그 국가엔 그 유형이 없다"와 "집계에서 누락됐다"가 구별되지 않는다.
    types: list[str] = []
    for _, s in rows:
        for t in s.get("by_achievement_type", {}):
            if t not in types:
                types.append(t)
    if types:
        body.append("| **성과유형** | " + " | ".join("" for _ in rows) + " |")
        for t in sorted(types):
            body.append(
                f"| {t} | "
                + " | ".join(str(s.get("by_achievement_type", {}).get(t, 0)) for _, s in rows)
                + " |"
            )
```

Run: 같은 명령 → PASS

- [ ] **Step 7: `collect_country_analyses` (실패 테스트 먼저)**

```python
def test_collect_requires_every_requested_country(db_session):
    """요청한 국가 중 하나라도 done 분석이 없으면 ValueError —
    일부만으로 만들면 '그 국가는 성과가 없다'로 오독된다."""
    import pytest
    from app.services import comparison

    with pytest.raises(ValueError, match="US"):
        comparison.collect_country_analyses(db_session, subfield_id=1, year=2026,
                                            countries=["KR", "US"])
```

픽스처로 KR done 분석 1건만 심어둔다(`test_api.py`의 `_ANALYSIS` 조립 방식을 따를 것 —
`country` 필드를 반드시 채운다. `default=`는 INSERT 시점에만 적용돼 직접 만든 객체에는
안 들어간다).

- [ ] **Step 8: 실패 확인 후 구현**

```python
def collect_country_analyses(
    db: Session, subfield_id: int, year: int, countries: list[str]
) -> list[tuple[str, Analysis]]:
    """요청된 국가의 done 분석을 요청 순서대로 돌려준다.

    하나라도 없으면 ValueError(→409). 일부 국가만으로 비교 보고서를 만들면
    "그 국가는 성과가 없다"로 오독되므로, 부분 생성을 아예 막는다.
    """
    found = {
        a.country: a
        for a in db.query(Analysis).filter(
            Analysis.subfield_id == subfield_id,
            Analysis.year == year,
            Analysis.country.in_(countries),
            Analysis.status == "done",
        )
        # 본문이 빈 분석(논문 0건)은 합성에 넣어봐야 모델이 지어낼 여지만 준다
        if a.report_md
    }
    missing = [c for c in countries if c not in found]
    if missing:
        raise ValueError(
            f"{year}년 완성된 분석이 없는 국가: {', '.join(missing)}"
        )
    return [(c, found[c]) for c in countries]
```

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_comparison.py -v`
Expected: PASS

- [ ] **Step 9: 커밋**

```bash
cd backend && ./.venv/bin/python -m pytest
git add backend/app/services/comparison.py backend/tests/test_comparison.py
git commit -m "feat: 국가 비교 대조표 조립 — 숫자는 코드가 만든다"
```

---

## Task 3: `COMPARE_INSTRUCTION` — 금지 조항이 본체다

**Files:**
- Modify: `backend/app/prompts.py`
- Test: `backend/tests/test_comparison.py` (추가)

**Interfaces:**
- Consumes: `REPORT_FORMAT_RULES`(기존)
- Produces: `COMPARE_INSTRUCTION`

이 프롬프트는 **금지 조항이 본체**다. 로드맵 점검에서 검증된 방식(못박지 않으면 모델이
뭉갠다)을 그대로 따른다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_compare_instruction_forbids_the_known_traps():
    """비교 보고서가 반복적으로 저지르는 오독을 프롬프트가 직접 금지해야 한다.
    각 항목은 실측으로 확인된 함정이라 문구를 약화시키면 안 된다."""
    from app.prompts import COMPARE_INSTRUCTION

    # 보고서 길이 = 압축률 차이. 실측: CN 731건 분석 → 4,813자,
    # KR 245건 분석 → 10,549자. 금지하지 않으면 "중국이 빈약하다"로 뒤집힌다.
    assert "길이" in COMPARE_INSTRUCTION
    # 표본율이 다른 국가끼리 인용수·논문수 비교 금지
    assert "표본율" in COMPARE_INSTRUCTION
    # 참여 기준 중복 계상 — 국가별 합계는 총합과 다르다
    assert "중복" in COMPARE_INSTRUCTION
    # 순위·점수 생성 금지
    assert "순위" in COMPARE_INSTRUCTION
    # 한계 절 강제
    assert "한계" in COMPARE_INSTRUCTION


def test_compare_instruction_forbids_recomputing_the_table():
    """대조표는 코드가 만든다. 모델이 다시 계산하면 틀린다."""
    from app.prompts import COMPARE_INSTRUCTION

    assert "계산" in COMPARE_INSTRUCTION
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_comparison.py -k compare_instruction -v`
Expected: FAIL — `ImportError: cannot import name 'COMPARE_INSTRUCTION'`

- [ ] **Step 3: 프롬프트 작성**

`backend/app/prompts.py`의 `ROADMAP_CHECK_INSTRUCTION` 앞(또는 뒤)에 추가:

```python
COMPARE_INSTRUCTION = """당신은 국가별 연구성과를 대조하는 과학기술 분석가입니다.
아래는 같은 세부기술·같은 연도에 대한 국가별 성과 분석 보고서와, 코드로 집계한 대조표입니다.
이를 바탕으로 정책·기획 담당자가 읽을 국가 비교 보고서를 마크다운으로 작성하세요.
개조식 나열이 아니라 문단으로 풀어 쓴 서술형이어야 합니다.

""" + REPORT_FORMAT_RULES + """

## 반드시 지킬 것

**대조표의 숫자를 다시 계산하지 마세요.** 표에 있는 값을 그대로 인용하고, 표에 없는
수치는 만들지 마세요. 비율·합계·증감을 새로 구하지 마세요.

**보고서 길이를 내용의 깊이로 읽지 마세요.** 논문이 많은 국가일수록 보고서가 여러 단계로
압축되어 오히려 짧습니다. 실제로 논문을 3배 분석한 국가의 보고서가 절반 길이인 경우가
있습니다. 길이는 말뭉치 크기에 따른 압축률 차이일 뿐이므로, 짧은 보고서를 "연구가
빈약하다"로 해석해서는 안 됩니다. 판단은 대조표의 분석 건수로 하세요.

**표본율이 100% 미만인 국가의 논문 수·인용수를 다른 국가와 직접 비교하지 마세요.**
그 국가는 인용 상위 N건만 수집된 것이라 인용수가 구조적으로 높게 나옵니다. 언급할
때마다 "상위 N건 기준"임을 병기하세요.

**분석 건수의 차이를 연구량의 차이로 단정하지 마세요.** abstract 결측률이 국가마다
다르며(출판사 구성 차이), 이는 연구량과 무관합니다.

**단독·주도·참여는 참여 기준이라 중복 계상됩니다.** 국제공동연구는 참여한 모든 국가에
계상되므로 국가별 수를 더해도 전체 논문 수가 되지 않습니다. 합산하지 마세요.

**순위나 점수를 만들지 마세요.** "한국이 3위" 같은 서술을 금지합니다 — 표본 조건이
국가마다 달라 순위가 성립하지 않습니다.

**보고서에 없는 내용을 채우지 마세요.** 어느 국가의 보고서에 없는 주제는 "해당 보고서에서
확인되지 않음"으로 적고, 없다는 사실 자체를 성과 부재로 단정하지 마세요.

구성:

## 1. 비교 개요
대상 세부기술·연도·국가와 각국의 수집 조건을 밝힙니다. 대조표를 그대로 싣고,
표본율이 다른 국가가 있으면 그것이 무엇을 뜻하는지 한 문단으로 설명합니다.

## 2. 연구 규모와 구조
분석 건수, 단독/주도/참여 구성, 국제공동 양상을 대조합니다. 어느 국가가 자국 단독
연구 중심이고 어느 국가가 국제공동 중심인지, 그것이 무엇을 시사하는지 서술합니다.

## 3. 기술적 성과 대조
**이 절이 본문의 핵심입니다.** 각국 보고서에서 확인되는 연구 주제를 대조해, 어느
국가가 어디에 집중하고 있는지 여러 문단으로 서술합니다. 같은 주제를 다루더라도
접근 방향이 다르면 그 차이를 짚으세요. 국가별로 나눠 나열하지 말고 **주제별로 묶어**
서술해야 비교가 됩니다.

## 4. 한국의 위치
한국이 앞서 있는 지점과 다루어지지 않은 지점을 구체적으로 짚습니다.
근거 없이 격려하거나 비관하지 말고 보고서에서 확인되는 것만 쓰세요.

## 5. 이 비교의 한계
다음을 반드시 포함하세요.
- 영문 국제지 기준이라 각국의 자국어 학술지(중국 CNKI, 일본 J-STAGE 등)가 빠져 있음
- 표본율이 국가마다 다르다는 점과 그 영향
- abstract 결측률 차이(출판사 구성에서 오며 연구량과 무관)
- 참여 기준의 중복 계상

분량 지시:
- 3절이 가장 길어야 합니다. 국가 수와 각 보고서 분량에 비례해 충분히 서술하세요.
- 같은 내용을 반복하거나 표를 다시 풀어 쓰는 것으로 분량을 채우지 마세요.
"""
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_comparison.py -k compare_instruction -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
cd backend && ./.venv/bin/python -m pytest
git add backend/app/prompts.py backend/tests/test_comparison.py
git commit -m "feat: COMPARE_INSTRUCTION — 실측으로 확인된 오독 5종을 금지"
```

---

## Task 4: 큐잉 + 처리

**Files:**
- Modify: `backend/app/services/comparison.py`
- Test: `backend/tests/test_comparison.py` (추가)

**Interfaces:**
- Consumes: `collect_country_analyses`, `build_comparison_table`, `COMPARE_INSTRUCTION`,
  `gemini_sync.generate`, `settings.thinking_reduce`
- Produces:
  - `enqueue_comparison(db, subfield_id, year, countries) -> CountryComparison`
  - `async process_comparison(db, row) -> None`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_enqueue_normalizes_country_order(db_session):
    """국가 순서가 달라도 같은 행을 재사용한다 — 안 그러면 같은 비교가
    순서만 바꿔 여러 행으로 쌓인다."""
    from app.services import comparison

    a = comparison.enqueue_comparison(db_session, 1, 2026, ["US", "KR"])
    b = comparison.enqueue_comparison(db_session, 1, 2026, ["KR", "US"])
    assert a.id == b.id
    assert a.countries == "KR,US"


def test_enqueue_rejects_single_country(db_session):
    import pytest
    from app.services import comparison

    with pytest.raises(ValueError, match="2개"):
        comparison.enqueue_comparison(db_session, 1, 2026, ["KR"])


def test_enqueue_keeps_old_body_on_regenerate(db_session):
    """재생성 큐잉은 status만 pending으로 되돌리고 본문은 남긴다 —
    처리 완료 전까지 이전 보고서를 계속 보여주기 위해서다(FieldReport와 같음)."""
    from app.services import comparison

    row = comparison.enqueue_comparison(db_session, 1, 2026, ["KR", "US"])
    row.report_md = "# 이전 보고서"
    row.status = "done"
    db_session.commit()

    again = comparison.enqueue_comparison(db_session, 1, 2026, ["KR", "US"])
    assert again.status == "pending"
    assert again.report_md == "# 이전 보고서"
```

픽스처로 KR·US done 분석을 각각 심어둔다(`country`와 `report_md`를 반드시 채울 것).

- [ ] **Step 2: 실패 확인**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_comparison.py -k enqueue -v`
Expected: FAIL — `AttributeError: module 'app.services.comparison' has no attribute 'enqueue_comparison'`

- [ ] **Step 3: 구현**

`backend/app/services/comparison.py`에 추가:

```python
def enqueue_comparison(
    db: Session, subfield_id: int, year: int, countries: list[str]
) -> CountryComparison:
    """비교 보고서를 pending으로 큐잉한다(실제 LLM 호출은 runner가 한다).

    검증을 여기서 하는 이유는 FieldReport와 같다 — 관리자가 즉시 404/409를 받게 하고,
    큐잉해 놓고 나중에 조용히 failed되는 것을 피한다.

    국가 목록은 정렬해 저장한다. 같은 조합을 다른 순서로 요청해도 같은 행을 재사용하기
    위해서다 — 안 그러면 같은 비교가 순서만 바꿔 여러 행으로 쌓인다.
    """
    if db.get(Subfield, subfield_id) is None:
        raise LookupError(f"세부기술 {subfield_id}를 찾을 수 없습니다.")

    codes = sorted(set(c.strip().upper() for c in countries if c.strip()))
    if len(codes) < 2:
        raise ValueError("비교하려면 국가가 2개 이상이어야 합니다.")

    # 여기서 검증만 하고 결과는 버린다 — 처리 시점에 다시 읽는다(그 사이 바뀔 수 있다).
    collect_country_analyses(db, subfield_id, year, codes)

    key = ",".join(codes)
    row = (
        db.query(CountryComparison)
        .filter(
            CountryComparison.subfield_id == subfield_id,
            CountryComparison.year == year,
            CountryComparison.countries == key,
        )
        .one_or_none()
    )
    if row is None:
        row = CountryComparison(
            subfield_id=subfield_id, year=year, countries=key, generated_at=_utcnow()
        )
        db.add(row)
    row.status = "pending"
    row.error = None
    db.commit()
    db.refresh(row)
    return row


async def process_comparison(db: Session, row: CountryComparison) -> None:
    """pending 비교 보고서 하나를 실제로 생성한다. runner.loop이 호출한다.

    큐잉 이후 분석이 지워졌을 수 있으므로 여기서 다시 검증한다 — 빈 입력으로 LLM을
    부르면 없는 성과를 지어낸다(reduce_subfield의 no_data 가드와 같은 이유).
    """
    codes = row.countries.split(",")
    pairs = collect_country_analyses(db, row.subfield_id, row.year, codes)
    subfield = db.get(Subfield, row.subfield_id)
    name = subfield.name if subfield else str(row.subfield_id)

    table = build_comparison_table(
        [(code, json.loads(a.stats_json or "{}")) for code, a in pairs]
    )
    bodies = "\n\n".join(
        f"## {country_name(code)} 보고서\n{a.report_md}" for code, a in pairs
    )
    payload = (
        f"[세부기술: {name} / {row.year}년 / 비교 국가: "
        f"{', '.join(country_name(c) for c in codes)}]\n\n"
        f"### 대조표(코드 집계 — 다시 계산하지 마세요)\n{table}\n\n{bodies}"
    )

    logger.info(
        "[비교] %s %d년 %s — 보고서 %d건 합성", name, row.year, row.countries, len(pairs)
    )
    row.report_md = await gemini_sync.generate(
        COMPARE_INSTRUCTION, payload, thinking=settings.thinking_reduce
    )
    row.generated_at = _utcnow()
    row.source_count = len(pairs)
    row.status = "done"
    row.error = None
    db.commit()
```

필요한 import를 파일 상단에 추가한다(`json`, `datetime`, `Session`, `Analysis`,
`CountryComparison`, `Subfield`, `gemini_sync`, `COMPARE_INSTRUCTION`, `settings`).
`_utcnow`는 `reducer.py`의 것과 같은 방식으로 정의한다(`reducer.py`를 읽어 확인할 것).

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_comparison.py -v`
Expected: PASS

- [ ] **Step 5: 입력 조립 테스트 추가 (LLM 호출 없이)**

`gemini_sync.generate`를 monkeypatch해 **실제로 무엇이 들어가는지** 고정한다.
이 테스트가 이 태스크의 핵심이다 — 대조표가 빠지거나 세부 보고서가 섞여 들어가면
비용과 품질이 조용히 무너진다.

```python
async def test_process_sends_table_and_bodies_only(db_session, monkeypatch):
    """LLM 입력에 대조표와 각국 종합 보고서가 들어가고,
    sections_json(세부 보고서)은 들어가지 않는다.

    세부까지 넣으면 5개국에서 약 725KB가 되고 2단계에서 확인한 이중 압축을
    비교 단계에서 반복한다(실측: CN 세부 144,730자 vs 종합 4,813자)."""
    from app.clients import gemini_sync
    from app.services import comparison

    captured = {}

    async def fake_generate(instruction, payload, thinking=None):
        captured["payload"] = payload
        return "# 비교 보고서"

    monkeypatch.setattr(gemini_sync, "generate", fake_generate)

    row = comparison.enqueue_comparison(db_session, 1, 2026, ["KR", "US"])
    await comparison.process_comparison(db_session, row)

    p = captured["payload"]
    assert "대조표" in p
    assert "한국 보고서" in p and "미국 보고서" in p
    assert "세부기술: " in p          # 헤더로 무엇을 비교하는지 고정
    assert "SECTION" not in p         # sections_json이 새어들지 않았다
    assert row.status == "done"
    assert row.source_count == 2
```

픽스처의 KR 분석에 `sections_json`을 채워 두어야 이 테스트가 의미를 갖는다.

- [ ] **Step 6: 통과 확인 + 커밋**

```bash
cd backend && ./.venv/bin/python -m pytest
git add backend/app/services/comparison.py backend/tests/test_comparison.py
git commit -m "feat: 비교 보고서 큐잉·처리 — 세부 보고서는 입력에 넣지 않는다"
```

---

## Task 5: 잡 루프 연결 + `_process_report` 로그 일반화

**Files:**
- Modify: `backend/app/services/runner.py:609-655`
- Test: `backend/tests/test_runner.py` (추가)

**Interfaces:**
- Consumes: `comparison.process_comparison`
- Produces: `advance_field_reports`가 비교 행도 처리

`_process_report`는 현재 `row.field_id`를 로그에 직접 참조한다(`runner.py:644,648`).
비교 행은 `subfield_id`를 가지므로 **AttributeError**가 난다. 먼저 이를 고친다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_runner.py`에 추가:

```python
async def test_advance_processes_pending_comparison(db_session, monkeypatch):
    """비교 보고서도 잡 루프가 한 틱에 하나씩 처리한다.
    _process_report가 row.field_id를 직접 참조하면 여기서 AttributeError가 난다."""
    from app.models import CountryComparison
    from app.services import runner

    row = CountryComparison(
        subfield_id=1, year=2026, countries="KR,US",
        status="pending", generated_at=datetime(2026, 8, 3),
    )
    db_session.add(row)
    db_session.commit()

    called = {}

    async def fake_process(db, r):
        called["id"] = r.id
        r.status = "done"

    monkeypatch.setattr(runner.comparison, "process_comparison", fake_process)
    await runner.advance_field_reports(db_session)

    assert called["id"] == row.id
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_runner.py -k comparison -v`
Expected: FAIL

- [ ] **Step 3: `_process_report` 로그 일반화**

`runner.py:640-655`의 `_process_report`에서 `row.field_id` 참조를 제거한다:

```python
async def _process_report(db: Session, row, processor, label: str) -> None:
    """report 행 하나를 처리하고, 실패하면 status=failed + error로 남긴다.
    한 건의 실패가 루프 전체를 멈추지 않게 여기서 흡수한다(세부기술 잡의 advance와 대칭).

    row는 FieldReport·RoadmapCheck·CountryComparison 중 하나라 공통 컬럼이 id·year뿐이다
    — field_id를 직접 읽으면 비교 행에서 AttributeError가 난다.
    """
    try:
        logger.info("[%s] id=%d year=%d 처리 시작", label, row.id, row.year)
        await processor(db, row)
        logger.info("[%s] id=%d year=%d 완료", label, row.id, row.year)
    except Exception as e:
        logger.exception("[%s] id=%d year=%d 실패", label, row.id, row.year)
        db.rollback()
        # 이하 기존 코드 그대로
```

- [ ] **Step 4: 비교 처리 분기 추가**

`advance_field_reports`의 `check_row` 처리 뒤에 추가하고, docstring도 갱신한다:

```python
    compare_row = (
        db.query(CountryComparison)
        .filter(CountryComparison.status == "pending")
        .order_by(CountryComparison.id)
        .first()
    )
    if compare_row is not None:
        await _process_report(db, compare_row, comparison.process_comparison, "국가 비교")
```

`check_row` 분기에도 `return`을 추가해야 한다(현재는 마지막이라 없다).
import에 `CountryComparison`과 `comparison`을 추가한다.

- [ ] **Step 5: 통과 확인**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_runner.py -v`
Expected: PASS (기존 테스트 포함)

- [ ] **Step 6: 커밋**

```bash
cd backend && ./.venv/bin/python -m pytest
git add backend/app/services/runner.py backend/tests/test_runner.py
git commit -m "feat: 잡 루프가 비교 보고서를 처리 + _process_report 로그 일반화"
```

---

## Task 6: API — 큐잉·조회

**Files:**
- Modify: `backend/app/routers/admin.py`
- Modify: `backend/app/routers/public.py`
- Test: `backend/tests/test_api.py` (추가)

**Interfaces:**
- Produces:
  - `POST /api/admin/subfields/{subfield_id}/comparison?year=&countries=KR,US,CN`
  - `GET /api/subfields/{subfield_id}/comparison?year=&countries=KR,US,CN`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_api.py`에 추가:

```python
def test_enqueue_comparison_requires_two_countries(client, admin_headers):
    r = client.post(
        "/api/admin/subfields/1/comparison",
        params={"year": 2026, "countries": "KR"},
        headers=admin_headers,
    )
    assert r.status_code == 422


def test_enqueue_comparison_409_when_country_missing(client, admin_headers):
    """분석이 없는 국가를 요청하면 409 — 큐잉 시점에 알려준다."""
    r = client.post(
        "/api/admin/subfields/1/comparison",
        params={"year": 2026, "countries": "KR,JP"},
        headers=admin_headers,
    )
    assert r.status_code == 409
    assert "JP" in r.json()["detail"]


def test_get_comparison_404_before_generation(client):
    r = client.get(
        "/api/subfields/1/comparison", params={"year": 2026, "countries": "KR,US"}
    )
    assert r.status_code == 404


def test_get_comparison_normalizes_order(client, admin_headers):
    """조회도 국가 순서를 정규화해야 큐잉한 행을 찾는다."""
    client.post(
        "/api/admin/subfields/1/comparison",
        params={"year": 2026, "countries": "US,KR"},
        headers=admin_headers,
    )
    r = client.get(
        "/api/subfields/1/comparison", params={"year": 2026, "countries": "KR,US"}
    )
    assert r.status_code == 200
    assert r.json()["countries"] == ["KR", "US"]
    assert r.json()["status"] == "pending"
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_api.py -k comparison -v`
Expected: FAIL — 404 (라우트 없음)

- [ ] **Step 3: admin 엔드포인트 추가**

`backend/app/routers/admin.py`:

```python
@router.post("/subfields/{subfield_id}/comparison")
def enqueue_comparison(
    subfield_id: int, year: int, countries: str, db: Session = Depends(get_db)
):
    """국가 비교 보고서를 pending으로 큐잉한다. countries는 콤마 구분(예: KR,US,CN).

    2개 미만이거나 형식이 틀리면 422, 분석이 없는 국가가 있으면 409.
    """
    codes = [c.strip().upper() for c in countries.split(",") if c.strip()]
    if any(len(c) != 2 or not c.isalpha() for c in codes):
        raise HTTPException(status_code=422, detail="국가 코드는 두 글자 알파벳이어야 합니다.")
    try:
        row = comparison.enqueue_comparison(db, subfield_id, year, codes)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        # 국가 2개 미만은 요청 형식 문제(422), 분석 부재는 상태 충돌(409)
        if "2개" in str(e):
            raise HTTPException(status_code=422, detail=str(e))
        raise HTTPException(status_code=409, detail=str(e))
    return {
        "subfield_id": row.subfield_id,
        "year": row.year,
        "countries": row.countries.split(","),
        "status": row.status,
    }
```

- [ ] **Step 4: public 엔드포인트 추가**

`backend/app/routers/public.py`:

```python
@router.get("/subfields/{subfield_id}/comparison")
def get_comparison(
    subfield_id: int, year: int, countries: str, db: Session = Depends(get_db)
):
    """비교 보고서 조회 — 캐시만 읽는다(생성은 관리자만).

    pending/failed도 그대로 내려준다. 화면이 status로 폴링·경고를 판단한다
    (분야 보고서와 같은 규약).
    """
    key = ",".join(sorted(set(c.strip().upper() for c in countries.split(",") if c.strip())))
    row = (
        db.query(CountryComparison)
        .filter(
            CountryComparison.subfield_id == subfield_id,
            CountryComparison.year == year,
            CountryComparison.countries == key,
        )
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="비교 보고서가 아직 생성되지 않았습니다.")

    subfield = db.get(Subfield, subfield_id)
    return {
        "subfield_id": row.subfield_id,
        "subfield_name": subfield.name if subfield else None,
        "year": row.year,
        "countries": row.countries.split(","),
        "country_names": [country_name(c) for c in row.countries.split(",")],
        "status": row.status,
        "error": row.error,
        "report_md": row.report_md,
        "source_count": row.source_count,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
    }
```

- [ ] **Step 5: 통과 확인 + 커밋**

```bash
cd backend && ./.venv/bin/python -m pytest
git add backend/app/routers/ backend/tests/test_api.py
git commit -m "feat: 비교 보고서 큐잉·조회 API"
```

---

## Task 7: 화면 — 비교 보고서 전용 페이지

**Files:**
- Create: `frontend/src/pages/ComparisonPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/api.ts` (경로 주의: `src/lib/`이 아니라 `src/` 바로 아래)
- Modify: `frontend/package.json` (version minor)

**Interfaces:**
- Consumes: `GET /api/subfields/{id}/comparison`
- Produces: 라우트 `/subfields/:subfieldId/compare/:year?countries=KR,US`

**먼저 읽을 것**: `frontend/src/pages/FieldReportPage.tsx`(218줄) — 같은 큐잉·폴링 패턴을
이미 구현하고 있다. 폴링 주기, 배너 클래스(`banner banner-warn` / `banner-risk`),
`PROSE_CLASSES`, `remarkCjkFriendly` 플러그인 구성을 **그대로 가져다 쓰고 새로 조립하지
않는다**. 아래 코드는 그 파일의 구조를 따른 것이나, 실제 작성 전에 반드시 원본을 읽어
import 목록과 배너 마크업을 맞출 것.

- [ ] **Step 1: api.ts에 타입과 조회 함수 추가**

`frontend/src/api.ts` (기존 `get<T>(path, adminKey?)` 헬퍼를 쓴다):

```typescript
export interface Comparison {
  subfield_id: number;
  subfield_name: string | null;
  year: number;
  countries: string[];
  country_names: string[];
  status: string;
  error: string | null;
  report_md: string;
  source_count: number;
  generated_at: string | null;
}

export function getComparison(subfieldId: number, year: number, countries: string[]) {
  const q = countries.join(",");
  return get<Comparison>(`/subfields/${subfieldId}/comparison?year=${year}&countries=${q}`);
}
```

- [ ] **Step 2: 페이지 작성**

`frontend/src/pages/ComparisonPage.tsx`:

```tsx
import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkCjkFriendly from "remark-cjk-friendly";

import { getComparison, type Comparison } from "../api";
import { MARKDOWN_COMPONENTS, PROSE_CLASSES } from "../lib/prose";

export default function ComparisonPage() {
  const { subfieldId, year } = useParams();
  const [searchParams] = useSearchParams();
  const [data, setData] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 국가는 쿼리스트링으로 받는다 — 조합이 자유로워 라우트 세그먼트로 두면
  // 경로가 국가 수만큼 갈라진다.
  const countries = (searchParams.get("countries") ?? "").split(",").filter(Boolean);

  useEffect(() => {
    if (!subfieldId || !year || countries.length < 2) return;
    let alive = true;

    const load = () =>
      getComparison(Number(subfieldId), Number(year), countries)
        .then((d) => {
          if (!alive) return;
          setData(d);
          setError(null);
        })
        .catch((e) => alive && setError(e.message));

    load();
    // pending이면 계속 폴링한다. 분야 보고서와 같은 규약 —
    // 생성은 잡 루프가 한 틱에 하나씩 하므로 즉시 끝나지 않는다.
    const timer = setInterval(() => {
      if (data?.status === "pending" || data === null) load();
    }, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subfieldId, year, searchParams.get("countries"), data?.status]);

  if (countries.length < 2) {
    return <p className="banner banner-risk">비교하려면 국가가 2개 이상이어야 합니다.</p>;
  }
  if (error) return <p className="banner banner-risk">{error}</p>;
  if (!data) return <p>불러오는 중…</p>;

  return (
    <article className="mx-auto max-w-3xl px-4 py-6">
      <h1 className="mt-1 text-3xl font-bold tracking-tight text-ink">
        {data.subfield_name} {data.year}년 국가 비교
      </h1>
      <p className="mt-2 text-sm text-muted">{data.country_names.join(" · ")}</p>

      {data.status === "pending" && (
        <p className="mt-3 banner banner-warn">
          비교 보고서를 생성하고 있습니다. 완료되면 자동으로 갱신됩니다.
        </p>
      )}
      {data.status === "failed" && (
        <p className="mt-3 banner banner-risk">생성 실패: {data.error}</p>
      )}

      {data.report_md && (
        <div className={PROSE_CLASSES}>
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkCjkFriendly]}
            components={MARKDOWN_COMPONENTS}
          >
            {data.report_md}
          </ReactMarkdown>
        </div>
      )}
    </article>
  );
}
```

**간격은 4/8/12/16/24/40만 쓴다**(`spacing.test.ts`가 `.tsx` 전체를 훑어 강제한다 —
위 코드의 `px-4 py-6 mt-1 mt-2 mt-3`이 모두 허용값이다). 마크다운 표는
`MARKDOWN_COMPONENTS`가 `.table-scroll`로 감싸므로 따로 처리하지 않는다.

- [ ] **Step 3: 라우트 추가**

`frontend/src/App.tsx`의 `<Route path="/subfields/:subfieldId/:year" ... />` **앞에** 넣는다.
뒤에 두면 `:year` 자리에 `compare`가 먼저 매칭될 수 있다.

```tsx
<Route path="/subfields/:subfieldId/compare/:year" element={<ComparisonPage />} />
```

- [ ] **Step 4: 검증**

```bash
cd frontend && npm run build   # tsc -b — 타입 오류는 여기서만 잡힌다
cd frontend && npm run lint
cd frontend && npm test        # spacing.test.ts 포함
```

Expected: 3개 모두 통과. `npm run build`가 `remark-cjk-friendly` import를 못 찾으면
`FieldReportPage.tsx`의 실제 import 경로를 확인해 맞춘다.

- [ ] **Step 5: 버전 올리고 커밋**

`frontend/package.json`의 `version`을 minor 올린다(기능 추가).

```bash
git add frontend/
git commit -m "feat(frontend): 국가 비교 보고서 페이지"
```

---

## Task 8: 관리자 화면에서 비교 생성

**Files:**
- Modify: `frontend/src/pages/Admin.tsx`
- Modify: `frontend/src/api.ts`

**먼저 읽을 것**: `Admin.tsx`의 "분야 보고서" 탭 — 큐잉 버튼 + 상태 폴링 + 401 처리를
이미 하고 있다. 그 구조를 따른다.

- [ ] **Step 1: api.ts에 큐잉 함수 추가**

기존 `post<T>(path, body, adminKey?)` 헬퍼를 쓴다. 바디가 없는 큐잉이라 `{}`를 넘긴다
(다른 큐잉 함수들이 어떻게 하는지 `api.ts`에서 확인해 맞출 것).

```typescript
export function enqueueComparison(
  subfieldId: number,
  year: number,
  countries: string[],
  adminKey: string,
) {
  const q = countries.join(",");
  return post<{ subfield_id: number; year: number; countries: string[]; status: string }>(
    `/admin/subfields/${subfieldId}/comparison?year=${year}&countries=${q}`,
    {},
    adminKey,
  );
}
```

**401 처리는 기존 판별을 그대로 쓴다** — `ApiError.status === 401`이면 저장된 키를 지우고
인증 화면으로 되돌린다(`useAdminKey.ts`). 새 판별을 만들지 않는다.

- [ ] **Step 2: 세부기술 표에 "비교 생성" 추가**

세부기술 행에서 연도와 국가를 받아 큐잉한다. 국가 입력은 **스케줄 카드의 `countries`
입력과 같은 콤마 구분 텍스트 입력**을 쓴다 — 새 위젯을 만들지 않는다(`.input` 클래스).

성공하면 생성된 비교 페이지 링크(`/subfields/{id}/compare/{year}?countries=...`)를 띄운다.

409(분석이 없는 국가)를 받으면 그 메시지를 **그대로** 배너에 표시한다. 이 오류는
관리자가 바로 고칠 수 있는 것이라(그 국가를 먼저 실행) 메시지 원문이 중요하다 —
"생성에 실패했습니다" 같은 뭉뚱그린 문구로 바꾸지 말 것.

- [ ] **Step 3: 검증 + 커밋**

```bash
cd frontend && npm run build && npm run lint && npm test
git add frontend/
git commit -m "feat(frontend): 관리자에서 비교 보고서 생성"
```

---

## Task 9: 종단 검증

배포 후 실제 API로 확인한다. **미검증 항목을 검증된 것처럼 다루지 않는다.**

- [ ] **Step 1: 배포**

```bash
cd /home/dev/code/performance-review
docker compose up -d --build     # 프론트는 web 컨테이너 안에서 빌드된다
docker compose exec api alembic current    # 0018 (head) 확인
```

- [ ] **Step 2: 대상 선정**

**차세대 메모리반도체(subfield 24) 2025년 KR+CN**이 적당하다 — 이미 양국 분석이
`done`이고(KR 245건 / CN 731건), 보고서 길이가 역전된 사례라 **Task 3의 길이 금지
조항이 실제로 듣는지**를 바로 볼 수 있다.

```bash
A=$(grep -E '^ADMIN_KEY=' .env | cut -d= -f2-)
curl -sS -X POST -H "X-Admin-Key: $A" \
  "http://localhost:8003/api/admin/subfields/24/comparison?year=2025&countries=KR,CN"
```

- [ ] **Step 3: 확인 항목**

잡 루프가 처리한 뒤(최대 30초) 조회한다:

```bash
curl -sS "http://localhost:8003/api/subfields/24/comparison?year=2025&countries=KR,CN" \
  | python3 -m json.tool
```

| # | 확인 | 통과 기준 |
|---|---|---|
| ① | 대조표가 본문에 실렸는가 | 표에 731·245가 그대로 |
| ② | **길이 역전을 오독하지 않았는가** | "중국의 연구가 빈약" 류 서술이 없다 |
| ③ | 표본율 병기 | CN 인용수를 언급할 때 조건이 붙는다 |
| ④ | 참여 기준 합산을 안 했는가 | 국가별 수를 더한 값이 본문에 없다 |
| ⑤ | 순위·점수 없음 | "N위" 서술이 없다 |
| ⑥ | `## 5. 이 비교의 한계` 존재 | 4개 항목 포함 |
| ⑦ | 기존 화면 영향 없음 | 분야·세부기술 보고서가 그대로 |

②가 이 검증의 핵심이다. 실패하면 프롬프트 문구를 강화하되 **약화시키지는 말 것**
(로드맵 `goal_count` 사례와 같은 성질의 문제다).

- [ ] **Step 4: 결과 기록**

`docs/2026-08-01-expansion-findings.md`의 로드맵 표에서 5단계를 검증 완료로 바꾸고,
1~4단계와 같은 형식으로 "5단계 검증 결과" 절을 추가한다. 소요 시간과 비용을 남긴다.

---

## 이 계획이 다루지 않는 것

- **분야 단위 비교** — 세부기술 비교가 쌓인 뒤 그 위에 얹는다. 지금 만들 이유가 없다.
- **비교 일괄 실행**(`run-all` 상당) — 어떤 조합을 일괄로 돌릴지가 아직 불분명하다.
  개별 큐잉으로 몇 건 만들어 보고 정한다.
- **세부 보고서를 입력에 넣는 선택지** — Task 9 ②가 실패하고 프롬프트 강화로도
  해결되지 않을 때 비로소 검토한다.
- **Gemini 비용 실측 누적** — 별개 작업. `openalex_usage`와 같은 방식이 필요하다는
  것은 확인됐다(연구노트 참고).
