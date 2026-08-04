# 다국가 비교 보고서와 진입 경로 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 3개국 이상 비교에서 국가별 내용이 축약되지 않게 하고, 국가·비교 보고서에 화면에서 들어갈 수 있게 한다.

**Architecture:** 비교를 쌍별(KR vs X)로 생성해 `sections_json`에 보관하고 그 위에 종합 1콜을 얹는다(`analyses.sections_json`과 같은 패턴). 공개 화면은 보유한 국가·비교만 링크로 노출하고, 관리자 격자에 국가 축과 비교 일괄 생성을 더한다.

**Tech Stack:** FastAPI · SQLAlchemy · Alembic · pytest / React 19 · Vite · Tailwind · vitest

설계: [`docs/superpowers/specs/2026-08-04-multi-country-comparison-ux-design.md`](../specs/2026-08-04-multi-country-comparison-ux-design.md)

## Global Constraints

- 마이그레이션 head는 현재 **0018**. 새 리비전은 `down_revision = "0018"`.
- 새 모델·컬럼을 더하면 `app/models/__init__.py` 확인(이번엔 기존 모델에 컬럼만 추가).
- 백엔드 테스트는 `backend/.venv`로만 돈다. 워크트리엔 venv가 없으므로
  **`/home/dev/code/performance-review/backend/.venv/bin/python -m pytest`** 로 부른다.
- 프론트 워크트리는 `node_modules`가 없다 — 첫 작업 전에 `cd frontend && npm install`.
- 통계·숫자는 코드로만 만든다. LLM에 계산을 맡기지 않는다.
- 프론트 간격은 4/8/12/16/24/40(Tailwind `1·2·3·4·6·10`)만. `src/lib/spacing.test.ts`가 강제.
- 넓은 표는 `.table-scroll`로 감싼다.
- 프론트를 고치면 `frontend/package.json`의 `version`을 올린다(기능 추가 minor).
- `EXTRACTION_SCHEMA_VERSION`은 건드리지 않는다.
- 현재 프론트 버전 **0.26.0**, 백엔드 테스트 **331건** 통과가 기준선.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `backend/alembic/versions/0019_comparison_sections.py` (생성) | `country_comparisons.sections_json` |
| `backend/app/models/field.py` (수정) | `CountryComparison.sections_json` |
| `backend/app/prompts.py` (수정) | `COMPARE_SYNTHESIS_INSTRUCTION` 추가 |
| `backend/app/services/comparison.py` (수정) | 쌍별 생성 + 종합 |
| `backend/app/services/runner.py` (수정) | 보고서 처리를 분석 루프 뒤로 |
| `backend/app/routers/public.py` (수정) | `availability` 엔드포인트, `summary`에 국가 |
| `backend/app/routers/admin.py` (수정) | 대시보드에 국가 축, 비교 일괄 |
| `frontend/src/api.ts` (수정) | 새 타입·함수 |
| `frontend/src/components/CountryBar.tsx` (생성) | 보고서 상단 국가·비교 줄 |
| `frontend/src/pages/Report.tsx` (수정) | `CountryBar` 배치 |
| `frontend/src/pages/ComparisonPage.tsx` (수정) | 쌍별 상세 펼침 |
| `frontend/src/pages/FieldDetail.tsx` (수정) | 세부기술 표에 보고서 열 |
| `frontend/src/components/DashboardGrid.tsx` 또는 `Admin.tsx` (수정) | 국가 축 격자 |

**`CountryBar`를 별도 컴포넌트로 빼는 이유**: `Report.tsx`가 이미 415줄이고, 이 줄은
보고서 본문과 무관한 이동 수단이라 책임이 다르다.

---

## Task 1: `sections_json` 컬럼 + 마이그레이션 0019

**Files:**
- Modify: `backend/app/models/field.py` (`CountryComparison`)
- Create: `backend/alembic/versions/0019_comparison_sections.py`
- Test: `backend/tests/test_comparison.py`

**Interfaces:**
- Produces: `CountryComparison.sections_json: list` — `[{"name": "한국 vs 미국", "body": "..."}]`

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_comparison.py` 끝에 추가:

```python
def test_comparison_holds_pairwise_sections():
    """쌍별 보고서를 보관한다. analyses.sections_json과 같은 모양이다."""
    db = _session()
    row = CountryComparison(
        subfield_id=1, year=2026, countries="CN,KR,US",
        generated_at=datetime(2026, 8, 4),
        sections_json=[{"name": "한국 vs 미국", "body": "본문"}],
    )
    db.add(row)
    db.commit()

    saved = db.query(CountryComparison).one()
    assert saved.sections_json[0]["name"] == "한국 vs 미국"


def test_sections_default_is_empty_list():
    """기본값이 None이면 화면이 length를 읽다 터진다."""
    db = _session()
    db.add(CountryComparison(subfield_id=1, year=2026, countries="CN,KR",
                             generated_at=datetime(2026, 8, 4)))
    db.commit()
    assert db.query(CountryComparison).one().sections_json == []
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest tests/test_comparison.py -k "pairwise_sections or sections_default" -v`
Expected: FAIL — `TypeError: 'sections_json' is an invalid keyword argument`

- [ ] **Step 3: 컬럼 추가**

`backend/app/models/field.py`의 `CountryComparison`에서 `source_count` 바로 위에:

```python
    # 쌍별 비교 보고서. analyses.sections_json과 같은 모양이고 화면도 같은 펼침
    # 패턴을 쓴다. 3개국 이상에서 국가별 내용이 축약되지 않게 하는 장치다 —
    # 종합 1콜만 두면 국가가 늘수록 각 나라 몫이 줄어든다(2단계 이중 압축과 같은 문제).
    sections_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
```

`field.py` 상단 import에 `JSON`을 추가한다(현재 없다):

```python
from sqlalchemy import (
    JSON,
    Boolean,
    ...
)
```

- [ ] **Step 4: 통과 확인**

Run: 같은 명령
Expected: PASS

- [ ] **Step 5: 마이그레이션**

`backend/alembic/versions/0019_comparison_sections.py`:

```python
"""country_comparisons.sections_json — 쌍별 비교 보고서 보존

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "country_comparisons",
        sa.Column("sections_json", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("country_comparisons", "sections_json")
```

- [ ] **Step 6: 마이그레이션 검증**

컨테이너는 **메인 체크아웃**에서 빌드되므로 워크트리 파일을 복사해 임시 DB로 돌린다:

```bash
cd /home/dev/code/performance-review
W=.claude/worktrees/report-improvements
docker compose exec -T db psql -U perfrev -d postgres -q \
  -c "DROP DATABASE IF EXISTS scratch0019;" -c "CREATE DATABASE scratch0019;"
docker compose cp $W/backend/alembic/versions/0019_comparison_sections.py api:/app/alembic/versions/
docker compose cp $W/backend/app/models/field.py api:/app/app/models/field.py
D="postgresql://perfrev:perfrev@db:5432/scratch0019"
docker compose exec -T -e DATABASE_URL=$D api alembic upgrade head
docker compose exec -T db psql -U perfrev -d scratch0019 -c "\d country_comparisons"
docker compose exec -T -e DATABASE_URL=$D api alembic downgrade 0018
docker compose exec -T -e DATABASE_URL=$D api alembic upgrade head
docker compose exec -T db psql -U perfrev -d postgres -q -c "DROP DATABASE scratch0019;"
docker compose up -d --force-recreate api   # 복사한 파일 원상복구
```

Expected: 0001→0019 성공, 다운그레이드에서 컬럼 삭제, 재적용 성공.

- [ ] **Step 7: 전체 테스트 + 커밋**

```bash
cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest
git add backend/app/models/field.py backend/alembic/versions/0019_comparison_sections.py backend/tests/test_comparison.py
git commit -m "feat: CountryComparison.sections_json — 쌍별 비교 보존"
```

---

## Task 2: 쌍별 생성 + 종합

**Files:**
- Modify: `backend/app/prompts.py`
- Modify: `backend/app/services/comparison.py`
- Test: `backend/tests/test_comparison.py`

**Interfaces:**
- Consumes: `CountryComparison.sections_json`(Task 1), `build_comparison_table`, `compare_instruction`, `_with_table`
- Produces: `COMPARE_SYNTHESIS_INSTRUCTION`, `process_comparison`이 쌍별을 저장

**핵심 규칙**
- 기준국은 **목록의 첫 국가가 아니라 `KR`**이다. `countries`는 정렬 저장이라 `CN,KR,US`처럼
  KR이 가운데 온다. KR이 없으면 알파벳 첫 국가를 기준으로 한다.
- **2개국이면 종합을 건너뛴다** — 쌍이 하나뿐이라 그것이 곧 보고서다(현행 동작·비용 유지).

- [ ] **Step 1: 쌍 분해 실패 테스트**

```python
def test_pairs_are_against_korea_regardless_of_sort_order():
    """countries는 정렬 저장이라 KR이 가운데 온다(CN,KR,US). 첫 원소를 기준국으로
    삼으면 '중국 vs 미국'이 되어 '한국과의 비교'라는 목적을 잃는다."""
    assert comparison.pair_countries(["CN", "KR", "US"]) == [("KR", "CN"), ("KR", "US")]


def test_pairs_fall_back_to_first_when_korea_absent():
    assert comparison.pair_countries(["CN", "US"]) == [("CN", "US")]


def test_two_countries_make_a_single_pair():
    assert comparison.pair_countries(["CN", "KR"]) == [("KR", "CN")]
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest tests/test_comparison.py -k pair_countries -v`
Expected: FAIL — `AttributeError: module 'app.services.comparison' has no attribute 'pair_countries'`

- [ ] **Step 3: 구현**

`backend/app/services/comparison.py`의 `collect_country_analyses` 위에:

```python
# 비교의 기준국. "한국과의 비교"가 목적이므로 KR을 한쪽에 고정한다.
_BASE_COUNTRY = "KR"


def pair_countries(codes: list[str]) -> list[tuple[str, str]]:
    """비교할 국가 쌍 목록. 기준국(KR)을 한쪽에 고정한다.

    codes는 정렬 저장이라 KR이 가운데 올 수 있다(CN,KR,US) — 첫 원소를 기준으로
    삼으면 "중국 vs 미국"이 되어 목적을 잃는다. KR이 없으면 첫 국가를 기준으로 한다.
    """
    base = _BASE_COUNTRY if _BASE_COUNTRY in codes else codes[0]
    return [(base, c) for c in codes if c != base]
```

- [ ] **Step 4: 통과 확인**

Run: 같은 명령
Expected: PASS

- [ ] **Step 5: 종합 프롬프트 실패 테스트**

```python
def test_synthesis_instruction_forbids_repeating_the_pairwise_bodies():
    """종합이 쌍별 내용을 다시 쓰면 그게 곧 축약 압력이 된다 — 쌍별 상세가 이미
    보관되므로 종합은 국가를 가로지르는 관찰만 한다."""
    from app.prompts import COMPARE_SYNTHESIS_INSTRUCTION

    assert "반복" in COMPARE_SYNTHESIS_INSTRUCTION
    assert "가로질러" in COMPARE_SYNTHESIS_INSTRUCTION
    # 쌍별과 같은 금지 조항을 공유한다(길이·표본율·순위)
    for word in ("길이", "표본율", "순위"):
        assert word in COMPARE_SYNTHESIS_INSTRUCTION
```

- [ ] **Step 6: 실패 확인 후 프롬프트 작성**

Run: `... -k synthesis_instruction -v` → FAIL (ImportError)

`backend/app/prompts.py`의 `COMPARE_INSTRUCTION` 바로 뒤에:

```python
COMPARE_SYNTHESIS_INSTRUCTION = """당신은 국가별 연구성과 비교를 종합하는 과학기술 분석가입니다.
아래는 같은 세부기술·연도에 대해 **한국과 각 국가를 1:1로 대조한 보고서들**입니다.
이를 종합해 국가 전체를 가로지르는 관찰을 마크다운으로 작성하세요.

""" + REPORT_FORMAT_RULES + """

## 반드시 지킬 것

**쌍별 보고서의 내용을 반복하지 마세요.** 각 쌍의 상세는 이미 보관되어 독자가 펼쳐 볼 수
있습니다. 여기서 같은 서술을 다시 하면 분량만 늘고, 정작 종합에서만 보이는 것이 묻힙니다.

**여러 국가를 가로질러야 보이는 것**을 쓰세요. 예: 두 나라가 같은 방향인데 한 나라만
다른 지점, 한국이 모든 상대국에 대해 공통으로 앞서거나 비어 있는 영역, 상대국끼리
서로 다른 전략.

**보고서 길이를 내용의 깊이로 읽지 마세요.** 논문이 많은 국가일수록 여러 단계로 압축되어
오히려 짧습니다. 판단은 대조표의 분석 건수로 하세요.

**표본율이 100% 미만인 국가의 논문 수·인용수를 직접 비교하지 마세요.** 그 국가는 인용
상위 N건만 수집된 것이라 인용수가 구조적으로 높게 나옵니다.

**순위나 점수를 만들지 마세요.** 표본 조건이 국가마다 달라 순위가 성립하지 않습니다.

**표를 그리지 마세요.** 대조표는 시스템이 넣습니다.

구성:

## 1. 종합 관찰
국가 전체를 가로질러 보이는 흐름을 여러 문단으로 서술합니다.

## 2. 한국의 위치
모든 상대국을 함께 놓고 볼 때 한국이 앞선 지점과 비어 있는 지점을 짚습니다.
개별 상대국과의 대조는 쌍별 보고서에 있으므로 여기서는 공통점·차이의 패턴만 씁니다.

## 3. 이 종합의 한계
쌍별 비교의 한계(영문 국제지 기준, 표본율 차이, abstract 결측률 차이, 참여 기준
중복 계상)를 이어받되, 종합 과정에서 각 쌍의 세부가 생략되었다는 점도 밝힙니다.
"""
```

Run: 같은 명령 → PASS

- [ ] **Step 7: 처리 흐름 실패 테스트**

```python
async def test_three_countries_produce_pairwise_sections_and_a_synthesis(monkeypatch):
    """3개국이면 쌍별 2건 + 종합 1콜 = 3콜. 쌍별은 sections_json에 남는다."""
    from app.services import comparison as comp

    db = _session()
    sid = _seed(db, ("KR", "US", "CN"))

    calls = []

    async def fake_generate(system, user, *, thinking=None, **kw):
        calls.append(user)
        return f"# 결과 {len(calls)}"

    monkeypatch.setattr(comp.gemini_sync, "generate", fake_generate)
    row = comp.enqueue_comparison(db, sid, 2026, ["KR", "US", "CN"])
    await comp.process_comparison(db, row)

    assert len(calls) == 3
    assert [s["name"] for s in row.sections_json] == ["한국 vs 중국", "한국 vs 미국"]
    # 종합 입력에는 쌍별 결과가 들어간다
    assert "결과 1" in calls[-1] and "결과 2" in calls[-1]
    assert row.status == "done"


async def test_two_countries_skip_the_synthesis(monkeypatch):
    """쌍이 하나뿐이면 그것이 곧 보고서다 — 종합을 건너뛰어 현행 비용을 유지한다."""
    from app.services import comparison as comp

    db = _session()
    sid = _seed(db, ("KR", "US"))
    calls = []

    async def fake_generate(system, user, *, thinking=None, **kw):
        calls.append(user)
        return "# 쌍별 결과"

    monkeypatch.setattr(comp.gemini_sync, "generate", fake_generate)
    row = comp.enqueue_comparison(db, sid, 2026, ["KR", "US"])
    await comp.process_comparison(db, row)

    assert len(calls) == 1
    assert row.sections_json == []          # 펼칠 것이 없다
    assert "쌍별 결과" in row.report_md
```

`_seed`는 기존 헬퍼다(국가 목록을 받는다). 3개국을 심으려면 `_seed(db, ("KR","US","CN"))`.

- [ ] **Step 8: 실패 확인 후 `process_comparison` 재작성**

Run: `... -k "pairwise_sections_and_a_synthesis or skip_the_synthesis" -v` → FAIL

`process_comparison`의 본문을 다음으로 교체한다(docstring은 아래 것으로 갱신):

```python
async def process_comparison(db: Session, row: CountryComparison) -> None:
    """pending 비교 보고서 하나를 생성한다. runner.loop이 호출한다.

    **쌍별로 만든 뒤 종합한다.** 3개국 이상을 한 콜에 넣으면 국가가 늘수록 각 나라 몫이
    줄어든다 — 2단계에서 확인한 이중 압축과 같은 문제다. 한국과 각 국가를 1:1로 대조해
    sections_json에 보관하고, 그 위에 종합 1콜을 얹는다. 쌍별 분량은 국가 수와 무관하므로
    구조적으로 축약이 일어나지 않는다.

    2개국이면 종합을 건너뛴다 — 쌍이 하나뿐이라 그것이 곧 보고서다(현행 비용 유지).

    한 틱 안에서 순차로 콜을 던진다. 콜 단위로 쪼개면 4개국 1건에 2분, 110건이면
    55시간이라 일괄이 성립하지 않는다. 순차라 동시성은 늘지 않는다.
    """
    codes = row.countries.split(",")
    pairs = collect_country_analyses(db, row.subfield_id, row.year, codes)
    by_code = dict(pairs)
    subfield = db.get(Subfield, row.subfield_id)
    name = subfield.name if subfield else str(row.subfield_id)

    # stats_json은 JSON 컬럼이라 SQLAlchemy가 이미 dict로 준다 — json.loads를 부르면
    # TypeError가 난다(실측: 첫 실행이 여기서 failed).
    all_stats = [(code, a.stats_json or {}) for code, a in pairs]
    full_table = build_comparison_table(all_stats)

    sections: list[dict] = []
    for base, other in pair_countries(codes):
        stats_rows = [(base, by_code[base].stats_json or {}),
                      (other, by_code[other].stats_json or {})]
        table = build_comparison_table(stats_rows)
        bodies = "\n\n".join(
            f"## {country_name(c)} 보고서\n{by_code[c].report_md}" for c in (base, other)
        )
        payload = (
            f"[세부기술: {name} / {row.year}년 / 비교 국가: "
            f"{country_name(base)}, {country_name(other)}]\n\n"
            f"### 대조표(코드 집계 — 근거로만 쓰세요. 보고서에는 시스템이 넣습니다)\n"
            f"{table}\n\n{bodies}"
        )
        logger.info("[비교] %s %d년 — %s vs %s 생성", name, row.year, base, other)
        body = await gemini_sync.generate(
            compare_instruction(stats_rows), payload, thinking=settings.thinking_reduce
        )
        sections.append({
            "name": f"{country_name(base)} vs {country_name(other)}",
            "body": _with_table(body, table),
        })

    if len(sections) == 1:
        # 쌍이 하나뿐 — 그것이 곧 보고서다. 펼칠 것이 없으므로 sections는 비운다.
        row.report_md = sections[0]["body"]
        row.sections_json = []
    else:
        joined = "\n\n".join(f"## {s['name']}\n{s['body']}" for s in sections)
        payload = (
            f"[세부기술: {name} / {row.year}년 / 비교 국가: "
            f"{', '.join(country_name(c) for c in codes)}]\n\n"
            f"### 대조표(코드 집계 — 근거로만 쓰세요)\n{full_table}\n\n{joined}"
        )
        logger.info("[비교] %s %d년 — 쌍별 %d건 종합", name, row.year, len(sections))
        synthesis = await gemini_sync.generate(
            COMPARE_SYNTHESIS_INSTRUCTION, payload, thinking=settings.thinking_reduce
        )
        row.report_md = _with_table(synthesis, full_table)
        row.sections_json = sections

    row.generated_at = _time.utcnow()
    row.source_count = len(pairs)
    row.status = "done"
    row.error = None
    db.commit()
```

`prompts` import에 `COMPARE_SYNTHESIS_INSTRUCTION`을 추가한다.

> `_with_table`은 `## 1.`을 찾아 끼운다. 종합 프롬프트의 첫 절이 `## 1. 종합 관찰`이라
> 그대로 맞는다. 쌍별은 `COMPARE_INSTRUCTION`의 `## 1. 비교 개요`라 역시 맞는다.

- [ ] **Step 9: 통과 확인**

Run: `cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest tests/test_comparison.py -v`
Expected: 전부 PASS. 기존 `test_process_sends_table_and_bodies_only`가 깨지면
쌍별 경로 기준으로 기대를 고친다(2개국이므로 콜 1회, 표 삽입은 그대로).

- [ ] **Step 10: 커밋**

```bash
cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest
git add backend/app/prompts.py backend/app/services/comparison.py backend/tests/test_comparison.py
git commit -m "feat: 비교를 쌍별로 생성해 보관하고 종합을 얹는다"
```

---

## Task 3: 보고서 처리를 분석 루프 뒤로

**Files:**
- Modify: `backend/app/services/runner.py` (`loop()`)
- Test: `backend/tests/test_runner.py`

**Interfaces:**
- Consumes: 없음
- Produces: 없음(순서만 바뀐다)

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_runner.py`에 추가:

```python
async def test_loop_advances_analyses_before_reports(ctx, monkeypatch):
    """보고서 합성이 분석 파이프라인을 막지 않아야 한다.

    비교 하나가 쌍별 포함 최대 2분 걸리는데, 그것이 분석 루프보다 먼저 돌면
    그 틱의 세부기술 진행이 통째로 밀린다. 보고서는 파이프라인보다 우선이 아니다.
    """
    from app.services import runner

    order: list[str] = []

    async def fake_reports(db):
        order.append("reports")

    async def fake_advance(db, analysis):
        order.append("analysis")

    monkeypatch.setattr(runner, "advance_field_reports", fake_reports)
    monkeypatch.setattr(runner, "advance", fake_advance)
    monkeypatch.setattr(runner, "run_scheduled_if_due", lambda db: None)
    monkeypatch.setattr(runner, "resume_paused", lambda db: None)

    # test_runner.py의 기존 ctx 픽스처가 Field·Subfield를 심어 준다.
    db = ctx
    db.add(Analysis(subfield_id=1, year=2026, country="KR", status="pending",
                    query_hash="h"))
    db.commit()
    await runner._tick(db)

    assert order == ["analysis", "reports"]
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest tests/test_runner.py -k advances_analyses_before -v`
Expected: FAIL — `AttributeError: module 'app.services.runner' has no attribute '_tick'`

- [ ] **Step 3: 루프 본체를 `_tick`으로 뽑고 순서를 바꾼다**

`loop()`의 `try` 내부를 함수로 분리한다. 테스트가 `asyncio.sleep` 없이 한 틱만
돌릴 수 있어야 하기 때문이다.

```python
async def _tick(db: Session) -> None:
    """루프 한 주기. 테스트가 sleep 없이 한 틱만 돌릴 수 있게 분리했다.

    ★ 분석을 먼저 전진시키고 보고서를 나중에 처리한다. 비교 하나가 쌍별 포함 최대
    2분 걸리는데 그것이 앞에 있으면 그 틱의 세부기술 진행이 통째로 밀린다 —
    보고서 합성은 검색·추출 파이프라인보다 우선이 아니다.
    """
    run_scheduled_if_due(db)
    resume_paused(db)
    # report_md(건당 12KB 규모)와 stats_json은 advance()가 읽지 않는다 — defer하지
    # 않으면 30초마다 활성 분석 전체의 보고서 본문을 통째로 읽어온다.
    active = (
        db.query(Analysis)
        .filter(Analysis.status.in_(ACTIVE_STATES))
        .options(defer(Analysis.report_md), defer(Analysis.stats_json))
        .all()
    )
    for analysis in active:
        await advance(db, analysis)
    await advance_field_reports(db)


async def loop() -> None:
    """미완 잡을 주기적으로 스캔해 전진시킨다. 상태가 전부 DB에 있으므로
    프로세스가 죽었다 살아나도 그대로 이어진다."""
    logger.info("잡 루프 시작 (%d초 간격)", settings.loop_interval_seconds)
    while True:
        try:
            db = SessionLocal()
            try:
                await _tick(db)
            finally:
                db.close()
        except Exception:
            logger.exception("잡 루프 순회 실패 — 다음 주기에 재시도")
        await asyncio.sleep(settings.loop_interval_seconds)
```

테스트는 `test_runner.py`의 기존 `ctx` 픽스처(`create_engine("sqlite://")` + Field·Subfield
시드)를 받아 `Analysis` 하나를 심는다 — 이 파일이 이미 쓰는 방식이다.

- [ ] **Step 4: 통과 확인 + 커밋**

```bash
cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest
git add backend/app/services/runner.py backend/tests/test_runner.py
git commit -m "fix: 보고서 합성을 분석 루프 뒤로 — 2분 블로킹이 파이프라인을 막았다"
```

---

## Task 4: 공개 API — 보유 현황

**Files:**
- Modify: `backend/app/routers/public.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Produces:
  - `GET /api/subfields/{id}/availability?year=` →
    `{"countries": ["KR","CN"], "comparisons": [{"countries": ["CN","KR"], "label": "한국 vs 중국"}]}`
  - `GET /api/fields/{id}/summary?year=` 응답의 각 행에 `countries: ["KR","CN"]` 추가
  - `GET /api/subfields/{id}/comparison` 응답에 `sections: [{"name","body"}]` 추가

- [ ] **Step 1: 실패하는 테스트**

```python
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
    assert got["comparisons"] == [{"countries": ["CN", "KR"], "label": "중국 vs 한국"}]


def test_field_summary_rows_carry_countries(client):
    db = app.dependency_overrides[get_db]()
    _seed_countries(db, ("KR", "CN"), year=2026)
    db.close()

    rows = client.get("/api/fields/1/summary", params={"year": 2026}).json()["rows"]
    assert rows[0]["countries"] == ["CN", "KR"]
```

`_seed_countries`는 기존 헬퍼다. `CountryComparison`·`datetime` import를 파일 상단에 추가한다.

- [ ] **Step 2: 실패 확인**

Run: `cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest tests/test_api.py -k "availability or rows_carry_countries" -v`
Expected: FAIL — 404 / KeyError

- [ ] **Step 3: `availability` 엔드포인트**

`backend/app/routers/public.py` 끝에:

```python
@router.get("/subfields/{subfield_id}/availability")
def subfield_availability(subfield_id: int, year: int, db: Session = Depends(get_db)):
    """이 세부기술·연도에 완성된 국가 분석과 비교 보고서 목록.

    화면의 국가 줄이 이것만 보고 링크를 만든다. **미보유는 아예 내려주지 않는다** —
    공개 화면 방문자에게 "아직 안 돌렸다"는 운영 사정을 보일 이유가 없다(그 정보가
    필요한 사람은 관리자이고, 관리자 격자가 전부 보여준다).
    """
    countries = sorted(
        a.country
        for a in db.query(Analysis.country).filter(
            Analysis.subfield_id == subfield_id,
            Analysis.year == year,
            Analysis.status == "done",
        )
    )
    comparisons = []
    for c in (
        db.query(CountryComparison)
        .filter(
            CountryComparison.subfield_id == subfield_id,
            CountryComparison.year == year,
            CountryComparison.status == "done",
        )
        .order_by(CountryComparison.countries)
    ):
        codes = c.countries.split(",")
        comparisons.append({
            "countries": codes,
            "label": " vs ".join(country_name(x) for x in codes),
        })
    return {"countries": countries, "comparisons": comparisons}
```

`Analysis.report_md`가 빈 분석도 `done`일 수 있으나, 여기서는 링크 유무만 판단하므로
`status == "done"`으로 충분하다(본문이 비면 그 화면이 스스로 안내한다).

- [ ] **Step 4: `field_summary`에 국가 추가**

`field_summary`의 `analyses_by_subfield` 조회는 현재 국가를 구분하지 않는다.
국가별 목록을 따로 한 번에 읽어 붙인다(세부기술마다 질의하면 55번 나간다):

```python
    countries_by_subfield: dict[int, list[str]] = {}
    for row in db.query(Analysis.subfield_id, Analysis.country).filter(
        Analysis.subfield_id.in_([s.id for s in subfields]),
        Analysis.year == year,
        Analysis.status == "done",
    ):
        countries_by_subfield.setdefault(row.subfield_id, []).append(row.country)
```

그리고 `rows.append(...)`에 한 줄:

```python
            "countries": sorted(countries_by_subfield.get(s.id, [])),
```

- [ ] **Step 5: `get_comparison`에 `sections` 추가**

`get_comparison`의 응답 dict(현재 `public.py:563` 부근)에 한 줄을 더한다 — 화면이 쌍별
상세를 펼치려면 필요하다. 분석 조회(`get_by_subfield_year`)는 이미 `sections`를 내려주므로
같은 이름을 쓴다.

```python
        "sections": row.sections_json,
```

테스트:

```python
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
```

- [ ] **Step 6: 통과 확인 + 커밋**

```bash
cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest
git add backend/app/routers/public.py backend/tests/test_api.py
git commit -m "feat: 보유 국가·비교 조회 API (미보유는 내려주지 않는다)"
```

---

## Task 5: 관리자 API — 격자의 국가 축과 비교 일괄

**Files:**
- Modify: `backend/app/routers/admin.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Produces:
  - `GET /api/admin/dashboard` 각 `years[]` 항목에 `country` 추가
  - `GET /api/admin/comparison-grid?year=` →
    `{"countries": ["KR","US","CN"], "rows": [{"subfield_id", "subfield_name", "analyses": {"KR": "done"}, "comparisons": {"KR,US": "done"}}]}`
  - `POST /api/admin/comparisons/run-all?year=&mode=pairs|all`

- [ ] **Step 1: 실패하는 테스트**

```python
def test_comparison_grid_shows_configured_countries_only(client):
    """열은 schedule_settings.countries에 설정된 국가만 — 안 쓰는 나라 열이 늘어붙지 않게."""
    db = app.dependency_overrides[get_db]()
    _seed_countries(db, ("KR", "CN"), year=2026)
    db.close()
    client.put("/api/admin/schedule",
               json={"enabled": True, "day": 10, "hour": 3, "years_back": 1,
                     "countries": "KR,CN"},
               headers={"X-Admin-Key": settings.admin_key})

    got = client.get("/api/admin/comparison-grid", params={"year": 2026},
                     headers={"X-Admin-Key": settings.admin_key}).json()
    assert got["countries"] == ["KR", "CN"]
    row = got["rows"][0]
    assert row["analyses"]["KR"] == "done"
    assert row["comparisons"] == {}          # 아직 만든 비교가 없다


def test_run_all_comparisons_skips_subfields_missing_a_country(client):
    """상대국 분석이 없는 세부기술은 조용히 건너뛴다(field-reports/run-all과 같은 규약)."""
    db = app.dependency_overrides[get_db]()
    _seed_countries(db, ("KR",), year=2026)      # CN 없음
    db.close()
    client.put("/api/admin/schedule",
               json={"enabled": True, "day": 10, "hour": 3, "years_back": 1,
                     "countries": "KR,CN"},
               headers={"X-Admin-Key": settings.admin_key})

    r = client.post("/api/admin/comparisons/run-all",
                    params={"year": 2026, "mode": "pairs"},
                    headers={"X-Admin-Key": settings.admin_key})
    assert r.status_code == 200
    assert r.json() == {"queued": 0, "skipped": 1}
```

- [ ] **Step 2: 실패 확인**

Run: `... -k "comparison_grid or run_all_comparisons" -v` → FAIL(404)

- [ ] **Step 3: 격자 엔드포인트**

`backend/app/routers/admin.py`에:

```python
@router.get("/comparison-grid")
def comparison_grid(year: int, db: Session = Depends(get_db)):
    """세부기술 × (국가 분석 · 비교 보고서) 현황.

    열은 schedule_settings.countries에 설정된 국가만 — 안 쓰는 나라 열이 늘어붙으면
    격자가 읽히지 않는다. 세부기술마다 질의하면 55번 나가므로 두 테이블을 각각
    한 번에 읽어 subfield_id로 묶는다(field_reports_overview와 같은 방식).
    """
    cfg = runner.get_schedule_settings(db)
    countries = [c.strip().upper() for c in (cfg.countries or "KR").split(",") if c.strip()]
    subfields = db.query(Subfield).filter(Subfield.active.is_(True)).order_by(
        Subfield.field_id, Subfield.name
    ).all()
    ids = [s.id for s in subfields]

    analyses: dict[int, dict[str, str]] = {}
    for a in db.query(Analysis.subfield_id, Analysis.country, Analysis.status).filter(
        Analysis.subfield_id.in_(ids), Analysis.year == year
    ):
        analyses.setdefault(a.subfield_id, {})[a.country] = a.status

    comparisons: dict[int, dict[str, str]] = {}
    for c in db.query(
        CountryComparison.subfield_id, CountryComparison.countries, CountryComparison.status
    ).filter(CountryComparison.subfield_id.in_(ids), CountryComparison.year == year):
        comparisons.setdefault(c.subfield_id, {})[c.countries] = c.status

    return {
        "year": year,
        "countries": countries,
        "rows": [
            {
                "subfield_id": s.id,
                "subfield_name": s.name,
                "field_id": s.field_id,
                "analyses": analyses.get(s.id, {}),
                "comparisons": comparisons.get(s.id, {}),
            }
            for s in subfields
        ],
    }
```

- [ ] **Step 4: 일괄 생성 엔드포인트**

```python
@router.post("/comparisons/run-all")
def run_all_comparisons(year: int, mode: str = "pairs", db: Session = Depends(get_db)):
    """당해연도 전체 세부기술의 비교를 일괄 큐잉한다.

    mode=pairs — 기준국과 각 상대국의 1:1 비교를 각각 만든다(KR,US / KR,CN).
    mode=all   — 설정된 국가 전체를 한 보고서로(KR,US,CN).

    대상이 안 되는 세부기술(상대국 분석 없음)은 조용히 건너뛴다 —
    하나가 막혀 전체가 실패하면 안 된다(field-reports/run-all과 같은 규약).
    """
    if mode not in ("pairs", "all"):
        raise HTTPException(status_code=422, detail="mode는 pairs 또는 all이어야 합니다.")

    cfg = runner.get_schedule_settings(db)
    countries = sorted({c.strip().upper() for c in (cfg.countries or "KR").split(",") if c.strip()})
    if len(countries) < 2:
        raise HTTPException(
            status_code=409,
            detail="스케줄의 대상 국가가 2개 이상이어야 비교를 만들 수 있습니다.",
        )

    combos = (
        [sorted(pair) for pair in comparison.pair_countries(countries)]
        if mode == "pairs"
        else [countries]
    )

    queued = skipped = 0
    for subfield in db.query(Subfield).filter(Subfield.active.is_(True)):
        for combo in combos:
            try:
                comparison.enqueue_comparison(db, subfield.id, year, combo)
                queued += 1
            except (LookupError, ValueError):
                skipped += 1
    return {"queued": queued, "skipped": skipped}
```

`admin.py` import에 `CountryComparison`을 추가한다.

- [ ] **Step 5: 대시보드에 국가 추가**

`dashboard()`의 `db.query(Analysis.id, ...)`에 `Analysis.country`를 더하고,
`years[]` 항목 dict에 `"country": a.country`를 추가한다. 기존 화면은 이 필드를
무시하므로 깨지지 않는다.

- [ ] **Step 6: 통과 확인 + 커밋**

```bash
cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest
git add backend/app/routers/admin.py backend/tests/test_api.py
git commit -m "feat: 비교 현황 격자 + 일괄 생성 API"
```

---

## Task 6: 백엔드 배포 검증

- [ ] **Step 1: 머지·배포**

```bash
cd /home/dev/code/performance-review
docker compose up -d --build
docker compose exec api alembic current        # 0019 (head)
```

- [ ] **Step 2: 3개국 비교 생성**

CN·US 분석이 모두 있어야 한다. US가 없으면 이 검증은 Task 9(국가 실행) 뒤로 미룬다.
현재 있는 조합(KR+CN)으로 먼저 **2개국 경로가 안 바뀌었는지**부터 확인한다:

```bash
A=$(grep -E '^ADMIN_KEY=' .env | cut -d= -f2-)
curl -sS -X POST -H "X-Admin-Key: $A" \
  "http://localhost:8003/api/admin/subfields/24/comparison?year=2025&countries=KR,CN"
sleep 60
curl -sS "http://localhost:8103/api/subfields/24/comparison?year=2025&countries=KR,CN" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['status'], len(d['report_md']), len(d['sections']))"
```

Expected: `done <약 3500> 0` — 2개국은 종합을 건너뛰므로 `sections`가 비어 있다.

---

## Task 7: 공개 화면 — 국가 줄과 목록 진입

**Files:**
- Create: `frontend/src/components/CountryBar.tsx`
- Modify: `frontend/src/api.ts`, `frontend/src/pages/Report.tsx`, `frontend/src/pages/FieldDetail.tsx`
- Modify: `frontend/package.json` (version minor)

**먼저**: `cd frontend && npm install` (워크트리에 `node_modules`가 없다)

- [ ] **Step 1: api.ts에 타입·함수**

```typescript
export interface Availability {
  countries: string[];
  comparisons: { countries: string[]; label: string }[];
}

export function getAvailability(subfieldId: number, year: number) {
  return get<Availability>(`/subfields/${subfieldId}/availability?year=${year}`);
}
```

`api.ts`의 **`SummarySubfield`**(`FieldSummary.rows`의 원소 타입)에 `countries: string[]`를
추가한다.

- [ ] **Step 2: `CountryBar` 작성**

`frontend/src/components/CountryBar.tsx`:

```tsx
import { Link } from "react-router-dom";
import { COUNTRY_NAMES } from "../lib/countries";

// 보고서 상단의 국가·비교 이동 줄. Report.tsx가 이미 415줄이고 이 줄은 본문과
// 무관한 이동 수단이라 파일을 나눈다.
//
// **보유하지 않은 국가는 아예 그리지 않는다**(비활성 표시가 아니라 미표시).
// 공개 화면 방문자에게 "아직 안 돌렸다"는 운영 사정을 보일 이유가 없다 —
// 그 정보가 필요한 사람은 관리자이고 관리자 격자가 전부 보여준다.
export default function CountryBar({
  subfieldId,
  year,
  current,
  countries,
  comparisons,
}: {
  subfieldId: number;
  year: number;
  current: string;
  countries: string[];
  comparisons: { countries: string[]; label: string }[];
}) {
  // 고를 것이 없으면 줄 자체를 숨긴다(한국뿐이고 비교도 없는 경우).
  if (countries.length < 2 && comparisons.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2 print:hidden">
      {countries.map((c) => (
        <Link
          key={c}
          to={`/subfields/${subfieldId}/${year}?country=${c}`}
          aria-current={c === current ? "page" : undefined}
          className="btn btn-toggle btn-sm"
        >
          {COUNTRY_NAMES[c] ?? c}
        </Link>
      ))}
      {comparisons.map((cmp) => (
        <Link
          key={cmp.countries.join(",")}
          to={`/subfields/${subfieldId}/compare/${year}?countries=${cmp.countries.join(",")}`}
          className="btn btn-neutral btn-sm"
        >
          {cmp.label}
        </Link>
      ))}
    </div>
  );
}
```

`aria-current`가 `btn-toggle[aria-pressed="true"]`와 다르므로, 현재 국가 강조는
`aria-pressed={c === current}`로 바꿔 기존 CSS 계약을 그대로 쓴다.

- [ ] **Step 3: 국가명 표를 프론트에도 둔다**

`frontend/src/lib/countries.ts`:

```typescript
// 백엔드 prompts.COUNTRY_NAMES와 같은 표. 화면이 국가 코드를 보여줄 일이
// 여러 곳(국가 줄·관리자 격자)이라 한 곳에 둔다.
export const COUNTRY_NAMES: Record<string, string> = {
  KR: "한국", US: "미국", CN: "중국", JP: "일본", DE: "독일",
  GB: "영국", FR: "프랑스", TW: "대만", IN: "인도", CA: "캐나다",
};
```

- [ ] **Step 4: `Report.tsx`에 배치**

`useState`로 `availability`를 받고, 연도 이동 버튼이 있는 줄 아래에 `CountryBar`를 둔다.

```tsx
const [avail, setAvail] = useState<Availability | null>(null);
useEffect(() => {
  if (!data) return;
  getAvailability(data.subfield_id, data.year).then(setAvail).catch(() => setAvail(null));
}, [data?.subfield_id, data?.year]);
```

렌더:

```tsx
{avail && (
  <div className="mt-4">
    <CountryBar
      subfieldId={data.subfield_id}
      year={data.year}
      current={data.country}
      countries={avail.countries}
      comparisons={avail.comparisons}
    />
  </div>
)}
```

`data.subfield_id`·`data.country`·`data.year`는 분석 조회 응답에 이미 있다(확인함) —
백엔드를 손댈 필요가 없다.

- [ ] **Step 5: `FieldDetail.tsx` 표에 보고서 열**

세부기술 행에 국가 링크를 단다. `row.countries`가 빈 배열이면 `—`를 넣는다
(결측은 빈칸이 아니라 `—`로 표기한다는 규칙).

```tsx
<td className="py-2 pr-3">
  {row.countries.length === 0 ? (
    <span className="text-faint">—</span>
  ) : (
    <span className="flex flex-wrap gap-1">
      {row.countries.map((c) => (
        <Link key={c} to={`/subfields/${row.subfield_id}/${year}?country=${c}`}
              className="btn btn-toggle btn-sm">
          {COUNTRY_NAMES[c] ?? c}
        </Link>
      ))}
    </span>
  )}
</td>
```

머리행에 `<th>보고서</th>`를 더한다.

- [ ] **Step 6: 검증**

```bash
cd frontend && npm run build && npm run lint && npm test
```

Expected: 셋 다 통과(간격 테스트 포함).

- [ ] **Step 7: 버전 올리고 커밋**

```bash
git add frontend/
git commit -m "feat(frontend): 보고서 국가 줄 + 목록 진입 (미보유는 숨김)"
```

---

## Task 8: 비교 화면의 쌍별 펼침 · 관리자 격자

**Files:**
- Modify: `frontend/src/pages/ComparisonPage.tsx`, `frontend/src/api.ts`
- Create: `frontend/src/components/ComparisonGrid.tsx`
- Modify: `frontend/src/pages/Admin.tsx`

- [ ] **Step 1: `Comparison` 타입에 `sections` 추가**

```typescript
export interface Comparison {
  // …기존 필드…
  sections: { name: string; body: string }[];
}
```

백엔드는 Task 4 Step 5에서 이미 `sections`를 내려준다.

- [ ] **Step 2: `ComparisonPage`에 펼침**

`Report.tsx`의 `SectionSummaries`와 같은 규약(`?withSections=1` + `Switch`)을 쓴다.
그 컴포넌트의 구조를 그대로 따르되 이름표만 바꾼다:

```tsx
<p className="text-eyebrow font-bold uppercase tracking-[0.09em] text-muted">
  국가 비교 {i + 1} / {sections.length}
</p>
<h3 className="text-xl font-bold text-ink">{s.name}</h3>
```

안내 문구:

> 국가가 셋 이상이면 한국과 각 나라를 1:1로 대조한 뒤 종합합니다. 위 종합은 국가를
> 가로질러 보이는 것만 다루므로, 개별 대조는 여기서 봅니다.

- [ ] **Step 3: 관리자 격자**

`frontend/src/components/ComparisonGrid.tsx` — `GET /api/admin/comparison-grid`를 읽어
`● ○ —`로 그린다.

- `●` 완료 · `○` 없음 · `—` 상대국 분석이 없어 불가
- 표는 `.table-scroll`로 감싼다(열이 국가 수만큼 늘어난다)
- 상단에 연도 선택과 `[1:1 비교 일괄 생성]` `[다국 비교 일괄 생성]` 버튼
- `다국` 열은 설정 국가가 3개 이상일 때만 보여준다(2개면 1:1과 같다)
- 성공은 배너로 칠하지 않는다(`banner-ok`가 없는 이유) — 결과는 평문으로

`Admin.tsx`의 `TABS`에 `{ id: "comparison-grid", label: "국가 현황" }`을 더하고
기존 "국가 비교" 탭(개별 큐잉)은 그대로 둔다 — 임의 조합을 만드는 유일한 통로다.

- [ ] **Step 4: 스케줄 카드 안내 문구**

`ScheduleSection.tsx`의 `countries` 입력 옆에:

> 세부기술 분석의 국가별 일괄 실행은 여기서 합니다 — 이 목록에 국가를 넣고 "지금
> 실행"을 누르면 전체 세부기술 × 대상 연도 × 각 국가가 한 번에 큐잉됩니다.

- [ ] **Step 5: 검증 + 커밋**

```bash
cd frontend && npm run build && npm run lint && npm test
git add frontend/
git commit -m "feat(frontend): 쌍별 펼침 + 관리자 국가 현황 격자"
```

---

## Task 9: 국가 실행 — **실행 직전 승인 필요**

**이 태스크는 코드 변경이 아니다.** 실제 API 비용이 발생하므로 앞의 모든 태스크가
끝나고 배포된 뒤, **사용자 승인을 받고** 실행한다.

- [ ] **Step 1: 비용을 다시 제시하고 승인받기**

| 항목 | 추정 |
|---|---:|
| US (KR의 약 1.9배) | ~$15 |
| JP (KR의 약 0.87배) | ~$8 |
| **합계** | **~$23** |

추정 근거는 6개 세부기술 표본이라 실제와 다를 수 있다. OpenAlex 일일 예산($0.5)에
걸리면 자정에 자동 재개된다.

- [ ] **Step 2: 스케줄 국가 설정**

관리자 → 자동 스케줄 → 대상 국가를 `KR,US,CN,JP`로 저장.

- [ ] **Step 3: 실행**

관리자 → "지금 실행". 55개 세부기술 × 2개년 × 4국 = 440건이 큐잉된다.

- [ ] **Step 4: 진행 확인**

```bash
docker compose exec -T db psql -U perfrev -d perfrev -c "
SELECT country, status, count(*) FROM analyses GROUP BY country, status ORDER BY country;"
```

- [ ] **Step 5: 3개국 비교 생성 후 검증**

분석이 끝나면 차세대 메모리반도체 2025를 `KR,US,CN`으로 만들고 §8 검증표를 채운다:

| 확인 | 통과 기준 |
|---|---|
| **쌍별 분량 유지** | 3개국의 `한국 vs 중국` 쌍별이 2개국 때 본문(~3,500자)과 비슷하다 |
| 종합이 반복하지 않는가 | 쌍별 내용을 다시 쓰지 않는다 |
| 2개국 경로 불변 | 종합을 건너뛰고 1콜 |
| 대조표 | 코드가 넣은 표가 종합·쌍별 모두에 있다 |
| 소요 | 1건 2분 이내 |

- [ ] **Step 6: 결과 기록**

`docs/2026-08-01-expansion-findings.md`에 1~5단계와 같은 형식으로 절을 추가한다.
특히 **쌍별 분량이 유지됐는지의 실측값**을 남긴다 — 이 설계의 유일한 근거다.

---

## 이 계획이 다루지 않는 것

- **세부기술 분석의 일괄 생성 기능** — 이미 있다. 안내 문구만 더한다(Task 8 Step 4).
- **분야 단위 비교** — 세부기술 비교가 쌓인 뒤에 얹는다.
- **격자에서의 개별 셀 실행** — 일괄과 기존 개별 패널로 충분한지 먼저 본다.
- **한국이 빠진 조합의 격자 표시** — 관리자 패널의 콤마 입력으로만 만든다.
