# 세부 보고서 보존 + REDUCE 프롬프트 정비 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 3단 reduce가 만들고 버리던 그룹별 중간 보고서(partial)를 저장해 화면에서 펼쳐볼 수
있게 하고, `REDUCE_INSTRUCTION`에서 지켜지지 않는 지시를 실제 데이터로 확인된 것으로 교체한다.

**Architecture:** 현행 3단 reduce는 그룹별 partial 10개를 만든 뒤 최종 1콜로 **다시 압축하고
partial을 버린다.** 이 이중 압축이 500건 이상에서 인용률이 무너지는 직접 원인이다(실측:
단일 reduce 350–499구간 9.7% → 3단 500–799구간 5.6%). partial을 `analyses.sections_json`에
저장하고, 화면은 종합 보고서만 보여주되 토글로 이어붙인다 — 분야 보고서의 `withSub=1`과
같은 패턴이라 새 개념이 아니다. 아울러 오늘 사용자 신고로 드러난 인용 형식 문제와
정량 표 중복을 프롬프트에서 함께 잡는다.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Alembic / pytest · React 19 + Vite + Tailwind

## Global Constraints

- 테스트는 반드시 `backend/.venv`를 쓴다: `cd backend && ./.venv/bin/python -m pytest`
- **`EXTRACTION_SCHEMA_VERSION`을 올리지 않는다.** 이 계획은 `REDUCE_INSTRUCTION`만 건드리며,
  프롬프트 텍스트 변경은 추출 캐시를 무효화하지 않는다(`model_ver`가 프롬프트를 보지 않음).
- 마이그레이션 head는 현재 **0015**다. 새 리비전은 **0016**이며 `down_revision="0015"`.
- `alembic upgrade head`는 `docker-entrypoint.sh`가 컨테이너 기동 시 자동 실행한다.
- 기존 110건의 `sections_json`은 비어 있다(3단 reduce가 이미 지나갔음). 화면은 **비어 있으면
  토글 자체를 렌더하지 않는다** — 재생성 전까지는 아무것도 바뀌지 않아야 한다.
- 프론트 레이아웃 간격은 4/8/12/16/24/40(Tailwind `1·2·3·4·6·10`)만 쓴다. `spacing.test.ts`가 고정.
- 프론트를 고치면 `frontend/package.json`의 `version`을 올린다(기능 추가 → minor).

---

### Task 1: `analyses.sections_json` 컬럼과 모델

**Files:**
- Create: `backend/alembic/versions/0016_section_reports.py`
- Modify: `backend/app/models/analysis.py`
- Test: `backend/tests/test_models.py`

**Interfaces:**
- Produces: `Analysis.sections_json: list` — `[{"name": str, "body": str}, ...]` 형태.
  3단 reduce가 아닌 경우(단일 reduce) 빈 리스트.

리스트로 두는 이유: 그룹 **순서**가 보고서 구성 순서이고, dict를 쓰면 JSON 직렬화 시
순서 보장이 명세에 없다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_models.py` 끝에 추가:

```python
def test_analysis_sections_json_defaults_to_empty_list():
    """단일 reduce는 그룹이 하나뿐이라 세부 보고서가 없다 — 기본값이 빈 리스트여야
    화면이 '없음'을 판정할 수 있다."""
    db = _session()
    a = Analysis(subfield_id=1, year=2025, status="pending", query_hash="h")
    db.add(a)
    db.commit()
    db.refresh(a)
    assert a.sections_json == []


def test_analysis_sections_json_roundtrips_group_order():
    """그룹 순서가 보고서 구성 순서다 — 저장·조회에서 순서가 보존돼야 한다."""
    db = _session()
    sections = [{"name": "알고리즘", "body": "## 개괄\n본문"},
                {"name": "신소재", "body": "## 개괄\n다른 본문"}]
    a = Analysis(subfield_id=1, year=2026, status="done", query_hash="h",
                 sections_json=sections)
    db.add(a)
    db.commit()
    db.refresh(a)
    assert [x["name"] for x in a.sections_json] == ["알고리즘", "신소재"]
```

`_session()`은 `test_models.py`에 이미 있는 헬퍼다(인메모리 sqlite + `create_all`).

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_models.py -k sections -v`
Expected: FAIL — `TypeError: 'sections_json' is an invalid keyword argument for Analysis`

- [ ] **Step 3: 모델에 컬럼을 추가한다**

`backend/app/models/analysis.py`의 `extracted_this_run` 컬럼 **아래**에 추가:

```python
    # 3단 reduce가 만든 그룹별 중간 보고서. [{"name": 그룹명, "body": 마크다운}, ...]
    # 현행은 최종 통합 1콜로 다시 압축하면서 이것을 버렸는데, 그 이중 압축이 500건 이상에서
    # 인용률이 무너지는 직접 원인이다(실측: 단일 reduce 350~499구간 9.7% → 3단 500~799구간
    # 5.6%). 버리지 않고 남겨 화면에서 펼쳐볼 수 있게 한다.
    # dict가 아니라 리스트인 이유: 그룹 순서가 곧 보고서 구성 순서인데 JSON 객체의 키
    # 순서는 명세상 보장되지 않는다. 단일 reduce(그룹 1개)면 빈 리스트다.
    sections_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
```

- [ ] **Step 4: 마이그레이션을 만든다**

`backend/alembic/versions/0016_section_reports.py` 생성:

```python
"""analyses.sections_json — 3단 reduce의 그룹별 중간 보고서 보존

현행은 partial을 최종 통합 1콜로 다시 압축하면서 버렸다. 그 이중 압축이 500건 이상에서
인용률이 무너지는 원인이라(실측 9.7% → 5.6%), 버리지 않고 남겨 화면에서 펼쳐볼 수 있게 한다.

기존 행은 빈 리스트로 채운다 — 이미 3단 reduce가 지나가 partial이 남아 있지 않다.
해당 분석을 다시 실행해야 채워진다.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column("sections_json", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("analyses", "sections_json")
```

- [ ] **Step 5: 테스트 통과를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: PASS (전체)

- [ ] **Step 6: 마이그레이션이 실제 DB에 적용되는지 확인한다**

```bash
cd /home/dev/code/performance-review && docker compose up -d --build api
docker compose exec -T api alembic current
```
Expected: `0016 (head)`

```bash
docker compose exec -T db psql -U perfrev -d perfrev -c "\d analyses" | grep sections_json
```
Expected: `sections_json | json | not null`

- [ ] **Step 7: 커밋**

```bash
git add backend/app/models/analysis.py backend/alembic/versions/0016_section_reports.py backend/tests/test_models.py
git commit -m "feat(db): analyses.sections_json — 3단 reduce 중간 보고서 보존용 컬럼

기존 행은 빈 리스트다. 해당 분석을 다시 실행해야 채워진다."
```

---

### Task 2: `reduce_subfield`가 partial을 함께 반환

**Files:**
- Modify: `backend/app/services/reducer.py` (`reduce_subfield`)
- Modify: `backend/app/services/runner.py` (`_do_reduce`)
- Test: `backend/tests/test_reducer.py`, `backend/tests/test_runner.py`

**Interfaces:**
- Consumes: Task 1의 `Analysis.sections_json`
- Produces: `reducer.reduce_subfield(...) -> tuple[str, list[dict]]`
  — `(최종 보고서 마크다운, [{"name": 그룹명, "body": partial 마크다운}, ...])`.
  단일 reduce·데이터 없음이면 두 번째 원소는 `[]`.

**호출부는 `runner._do_reduce` 한 곳뿐이다**(`grep -rn "reduce_subfield" backend/app`으로 확인).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_reducer.py` 끝에 추가. 이 파일에 이미 있는 헬퍼
(`_FakeDb`, `_ANALYSIS`, `_ext`, `_FakeGenerate`)를 그대로 쓴다 — 이 테스트들은
DB 세션이 필요 없다(`reduce_subfield`는 `db.get(Subfield, ...)` 한 번만 부른다).

```python
async def test_reduce_subfield_returns_partials_for_three_tier(monkeypatch):
    """3단 reduce의 그룹별 중간 보고서를 버리지 않고 함께 돌려준다."""
    monkeypatch.setattr(settings, "reduce_group_threshold", 2)

    async def fake(system, user, *, thinking, **kwargs):
        return "부분보고서" if user.startswith("[성과유형:") else "최종 통합 보고서"

    monkeypatch.setattr(reducer.gemini_sync, "generate", fake)
    ext = [_ext(f"a{i}", "공정") for i in range(3)] + [_ext(f"b{i}", "알고리즘") for i in range(2)]
    papers = {e.paper_key: Paper(paper_key=e.paper_key, title=f"논문 {e.paper_key}",
                                 year=2026, journal="J", abstract="A",
                                 source="openalex", citations=1) for e in ext}

    report, sections = await reducer.reduce_subfield(_FakeDb(), _ANALYSIS, ext, papers)

    assert report == "최종 통합 보고서"
    assert len(sections) >= 2
    assert all(set(s) == {"name", "body"} for s in sections)
    assert all(s["body"] == "부분보고서" for s in sections)
    # 그룹명이 보존돼야 화면이 유형별 제목을 붙일 수 있다.
    assert "공정" in [s["name"] for s in sections]


async def test_reduce_subfield_returns_empty_sections_for_single_call(monkeypatch):
    """단일 reduce는 그룹이 하나뿐이라 세부 보고서가 없다."""
    fake = _FakeGenerate("단일 보고서")
    monkeypatch.setattr(reducer.gemini_sync, "generate", fake)
    ext = [_ext("k1", "공정")]
    papers = {"k1": Paper(paper_key="k1", title="논문", year=2026, journal="J",
                          abstract="A", source="openalex", citations=1)}

    report, sections = await reducer.reduce_subfield(_FakeDb(), _ANALYSIS, ext, papers)

    assert report == "단일 보고서"
    assert sections == []


async def test_reduce_subfield_no_data_returns_empty_sections():
    """추출 0건이면 LLM을 부르지 않고 안내문과 빈 세부 보고서를 돌려준다."""
    report, sections = await reducer.reduce_subfield(_FakeDb(), _ANALYSIS, [], {})

    assert "분석 대상 논문이 없어" in report
    assert sections == []
```

기존 테스트 중 `reduce_subfield`의 반환을 문자열로 받는 것들
(`test_reduce_subfield_skips_llm_when_body_empty_single_group`,
`test_reduce_subfield_skips_llm_when_all_groups_empty_three_tier`,
`test_three_tier_final_call_still_names_the_subfield` 등)을 **함께 고친다** —
`result, _ = await reducer.reduce_subfield(...)` 형태로 언패킹.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_reducer.py -k "partials or single_call or no_data_returns" -v`
Expected: FAIL — `ValueError: too many values to unpack` 또는 `AttributeError: 'str' object has no attribute ...`

- [ ] **Step 3: `reduce_subfield`의 반환 타입을 바꾼다**

`backend/app/services/reducer.py`의 `reduce_subfield` 시그니처와 모든 `return`을 고친다:

```python
async def reduce_subfield(
    db: Session,
    analysis: Analysis,
    extractions: list[PaperExtraction],
    papers_by_key: dict[str, Paper],
) -> tuple[str, list[dict]]:
    """세부기술 보고서를 만들고 (최종 보고서, 그룹별 중간 보고서)를 돌려준다.

    3단 reduce의 partial을 버리지 않는 이유: 최종 통합이 partial을 다시 압축하는
    이중 압축이 500건 이상에서 인용률이 무너지는 직접 원인이다(실측: 단일 reduce
    350~499구간 9.7% → 3단 500~799구간 5.6%). 화면이 이것을 펼쳐 보여준다.

    추출 결과가 0건이거나, 있어도 papers_by_key 매칭 실패로 LLM에 보낼 본문이 비면
    LLM을 호출하지 않는다 — 빈 입력으로 부르면 모델이 성과를 통째로 지어낸다.
    """
    no_data_message = "분석 대상 논문이 없어 성과를 정리할 수 없습니다."
    if not extractions:
        return no_data_message, []
```

이어서 `subfield`/`header`/`groups` 계산은 그대로 두고, 단일 그룹 분기를 고친다:

```python
    groups = group_for_reduce(extractions)
    if len(groups) == 1:
        body = format_extractions(next(iter(groups.values())), papers_by_key)
        if not body:
            logger.warning(
                "[reduce] 추출 %d건이 있으나 papers_by_key 매칭 실패로 본문이 비어 LLM 호출을 건너뜀",
                len(extractions),
            )
            return no_data_message, []
        report = await gemini_sync.generate(
            REDUCE_INSTRUCTION, header + body, thinking=settings.thinking_reduce
        )
        return report, []
```

3단 분기에서 partial을 구조화해 함께 모은다:

```python
    partials: list[str] = []
    sections: list[dict] = []
    for name, items in groups.items():
        body = format_extractions(items, papers_by_key)
        if not body:
            continue
        partial = await gemini_sync.generate(
            REDUCE_INSTRUCTION, f"[성과유형: {name}]\n{body}", thinking=settings.thinking_reduce
        )
        partials.append(f"### {name}\n{partial}")
        sections.append({"name": name, "body": partial})

    if not partials:
        logger.warning(
            "[reduce] 추출 %d건이 있으나 모든 그룹에서 papers_by_key 매칭 실패로 본문이 비어 LLM 호출을 건너뜀",
            len(extractions),
        )
        return no_data_message, []

    report = await gemini_sync.generate(
        REDUCE_INSTRUCTION,
        header
        + "아래는 성과유형별 중간 정리 결과입니다. 이를 하나의 보고서로 통합하세요.\n\n"
        + "\n\n".join(partials),
        thinking=settings.thinking_reduce,
    )
    return report, sections
```

- [ ] **Step 4: `_do_reduce`가 저장하게 한다**

`backend/app/services/runner.py`에서 `reduce_subfield` 호출부를 고친다:

```python
        analysis.report_md, analysis.sections_json = await reducer.reduce_subfield(
            db, analysis, extractions, papers_by_key
        )
        analysis.report_model_ver = current_model_ver
```

**보고서 재생성을 건너뛰는 분기(`skip_reduce`)에서는 `sections_json`을 건드리지 않는다** —
기존 값을 그대로 둬야 화면이 계속 세부 보고서를 보여준다.

- [ ] **Step 5: 재생성 생략 시 기존 세부 보고서가 보존되는지 테스트한다**

`backend/tests/test_runner.py` 끝에 추가:

```python
async def test_do_reduce_preserves_sections_when_skipping_regeneration(ctx, monkeypatch):
    """신규 추출이 없어 보고서 재생성을 건너뛸 때 기존 세부 보고서를 지우면 안 된다."""
    db, sf = ctx
    kept = [{"name": "알고리즘", "body": "이전 부분보고서"}]
    a = Analysis(subfield_id=sf.id, year=2025, status="reducing", query_hash="h",
                 report_md="기존 보고서", analyzed_count=0,
                 report_model_ver=mapper.model_ver(), sections_json=kept)
    db.add(a)
    db.commit()

    async def fail(*args, **kwargs):
        raise AssertionError("재생성을 건너뛰어야 한다")

    monkeypatch.setattr(runner.reducer, "reduce_subfield", fail)

    await runner.advance(db, a)
    db.refresh(a)
    assert a.sections_json == kept
    assert a.report_md == "기존 보고서"
```

`mapper`가 `test_runner.py`에 import돼 있지 않으면 파일 상단 import 목록에
`from app.services import mapper`를 추가한다.

- [ ] **Step 6: 전체 테스트를 통과시킨다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: PASS

기존 `test_reducer.py`의 3단 reduce 테스트들이 `reduce_subfield`의 반환을 문자열로
쓰고 있으면 **함께 고친다**(`report, _ = await reducer.reduce_subfield(...)`).

- [ ] **Step 7: 커밋**

```bash
git add backend/app/services/reducer.py backend/app/services/runner.py backend/tests/test_reducer.py backend/tests/test_runner.py
git commit -m "feat(reduce): 3단 reduce의 그룹별 중간 보고서를 버리지 않고 저장

최종 통합이 partial을 다시 압축하는 이중 압축이 500건 이상에서 인용률이
무너지는 직접 원인이다(실측 9.7% → 5.6%).

재생성 생략(skip_reduce) 시에는 sections_json을 건드리지 않는다 — 지우면
화면에서 세부 보고서가 사라진다."
```

---

### Task 3: `REDUCE_INSTRUCTION` 정비

**Files:**
- Modify: `backend/app/prompts.py` (`REDUCE_INSTRUCTION`)
- Test: `backend/tests/test_reducer.py`

**Interfaces:**
- Consumes: 없음
- Produces: 없음 (프롬프트 문자열만 변경)

**고칠 것 네 가지** — 전부 실측으로 근거가 있다.

1. **달성 불가능한 정량 목표 제거.** "인용 논문이 전체 항목의 25% 이상", "최소 8,000자"는
   100–199구간에서만 지켜지고 그 위로는 전부 미달이다(실측). 지켜지지 않는 지시가 남아
   있으면 모델이 다른 지시도 같은 강도로 따르지 않는다.
2. **인용 형식 강제.** LLM이 백틱(코드 스팬)으로 인용해 각주가 안 잡힌 사례가 있다
   (안전·신뢰 AI 2026: 서술부 인용 26건 중 백틱 23건).
3. **인용 위치 강제.** 인용을 문단이 아니라 불릿 목록으로 나열해 번호만 있는 항목이
   쌓인 사례가 있다(효율적 AI 2026: 서술부 불릿 30줄).
4. **정량 표 중복 제거.** `stats.aggregate_metrics`가 코드로 집계한 표를 화면이 이미
   보여주므로, LLM이 같은 표를 또 만들 이유가 없다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_reducer.py` 끝에 추가:

```python
def test_reduce_instruction_forbids_unreachable_quotas():
    """지켜지지 않는 정량 목표는 남아 있는 것이 해롭다 — 모델이 다른 지시도
    같은 강도로 따르지 않게 된다(실측: 25% 인용 목표가 200건 이상에서 전부 미달)."""
    from app.prompts import REDUCE_INSTRUCTION

    assert "25% 이상" not in REDUCE_INSTRUCTION
    assert "8,000자" not in REDUCE_INSTRUCTION


def test_reduce_instruction_pins_citation_format_and_position():
    """백틱 인용(각주 미인식)과 불릿 나열(번호만 남는 항목)을 실제로 겪었다."""
    from app.prompts import REDUCE_INSTRUCTION

    assert "백틱" in REDUCE_INSTRUCTION
    assert "문장 안" in REDUCE_INSTRUCTION


def test_reduce_instruction_delegates_metric_table_to_code():
    """정량 표는 stats.aggregate_metrics가 코드로 만든다 — LLM이 또 만들면 중복이다."""
    from app.prompts import REDUCE_INSTRUCTION

    assert "## 정량 성과 정리" not in REDUCE_INSTRUCTION
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_reducer.py -k reduce_instruction -v`
Expected: FAIL (3건)

- [ ] **Step 3: 프롬프트를 고친다**

`backend/app/prompts.py`의 `REDUCE_INSTRUCTION`에서 **"## 정량 성과 정리" 절 전체를 삭제**하고,
그 자리에 다음 문단을 넣는다:

```
정량 수치 표는 만들지 마세요. 추출된 수치는 코드가 전수 집계해 화면에 별도 표로
보여주므로(지표별 논문 수·중앙값·p90·최대), 여기서 같은 표를 만들면 중복입니다.
수치는 서술 안에서 근거로 인용할 때만 쓰세요.
```

같은 파일에서 **"분량 지시(정량 목표 — 반드시 지키세요):" 블록 전체**(`- 제공된 성과 항목이
**100건 이상**이면:` 부터 `- 목록에 있는데 아직 언급하지 않은 항목이 남아 있다면, 그것부터
서술에 포함하세요.` 까지)를 삭제하고 다음으로 교체한다:

```
분량과 인용:
- 항목이 많으면 유사한 연구를 주제별로 묶어 **대표 성과**를 인용하세요. 비슷한 연구
  수십 편을 모두 나열할 필요는 없습니다. 다만 묶었으면 **몇 편이 이 주제에 속하는지**를
  밝히세요(예: "고체전해질 계열 연구가 40여 편으로 가장 두텁고, 그중 대표적인 것은 ...").
- 재료를 더 깊이 파헤쳐 서술하세요. 같은 내용을 다른 말로 반복하거나, 일반론·상투적
  표현·전망성 문장으로 분량을 채우지 마세요.
```

**인용 규칙**(기존 "각 성과마다 근거 논문 제목을 괄호로 인용하세요" 부분)을 다음으로 교체한다:

```
각 성과의 근거 논문은 **문장 안에서 괄호로** 인용하세요.
- 괄호 안에는 논문 제목만 그대로 넣으세요. 연도, 저자명, 그 외 텍스트를 덧붙이지 마세요.
  예: (정확한 논문 제목). "([2025] 정확한 논문 제목)"처럼 연도를 붙이지 마세요.
- **백틱(`)으로 감싸지 마세요.** 백틱으로 쓰면 인용으로 인식되지 않아 제목이 그대로
  노출됩니다.
- **인용만 있는 불릿 목록을 만들지 마세요.** 인용은 그 성과를 설명하는 문장 안에
  들어가야 합니다. 문단 아래에 인용만 나열하면 읽는 사람이 어느 서술의 근거인지 알 수 없습니다.
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/app/prompts.py backend/tests/test_reducer.py
git commit -m "feat(prompts): REDUCE_INSTRUCTION 정비 — 지켜지는 지시로 교체

달성 불가능한 정량 목표(25% 인용·8,000자)를 제거하고 '유사 연구를 묶되
몇 편인지 밝혀라'로 바꾼다. 지켜지지 않는 지시는 남아 있는 것이 해롭다.

오늘 사용자 신고로 드러난 두 형식 문제를 명시적으로 금지한다:
백틱 인용(각주 미인식)과 인용만 있는 불릿 목록.

정량 표는 stats.aggregate_metrics가 코드로 만들므로 LLM 쪽 절을 삭제한다."
```

---

### Task 4: 세부 보고서 조회 API

**Files:**
- Modify: `backend/app/routers/public.py` (`_serialize`)
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: Task 1의 `Analysis.sections_json`, 기존 `_apply_footnotes`
- Produces: `GET /api/analyses/{id}` · `GET /api/subfields/{sid}/analyses/{year}` 응답에
  `sections: [{"name": str, "body_md": str}, ...]` 추가.
  **각주 치환이 적용된 본문**이며 참고문헌 번호는 종합 보고서와 공유한다.

각주를 공유하는 이유: 세부 보고서를 펼쳤을 때 `[12]`가 종합 보고서의 `[12]`와 다른 논문을
가리키면 읽는 사람이 혼란스럽다. `_apply_footnotes`를 종합+세부 본문에 **한 번에** 적용해
번호 체계를 하나로 만든다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_api.py` 끝에 추가:

```python
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
        db,
        "종합 서술입니다 (Solid Electrolyte Interface Engineering Study).",
        papers,
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_api.py -k sections -v`
Expected: FAIL — `KeyError: 'sections'`

- [ ] **Step 3: `_footnoted_report`가 세부 보고서까지 처리하게 한다**

`backend/app/routers/public.py`의 `_footnoted_report`를 고친다:

```python
def _footnoted_report(db: Session, analysis: Analysis) -> tuple[str | None, list[dict], list[dict]]:
    """analysis의 report_md와 세부 보고서에 각주 치환을 적용한다.

    종합 보고서와 세부 보고서를 **한 번에** 치환해 번호 체계를 공유한다 — 따로 매기면
    세부 보고서를 펼쳤을 때 [12]가 종합 보고서의 [12]와 다른 논문을 가리킨다.

    report_md 원문은 "(논문 제목)" 형태로 저장돼 있고, 각주 [n] 치환은 조회 시점에
    한다 — 세부기술 보고서 화면과 분야 종합보고서 부록(세부기술 첨부)이 이걸 공유한다.
    빼먹으면 논문 제목이 full name 그대로 노출된다.
    """
    # 각주는 id/title/journal/year/doi만 쓴다. 전체 ORM 행을 실으면 abstract(논문당
    # 1~2KB)까지 딸려와, 703건짜리 보고서 한 번 여는 데 1MB 가까이 헛읽는다.
    papers = db.query(
        Paper.id, Paper.title, Paper.journal, Paper.year, Paper.doi
    ).join(AnalysisPaper, AnalysisPaper.paper_id == Paper.id).filter(
        AnalysisPaper.analysis_id == analysis.id
    ).all()

    sections = analysis.sections_json or []
    # 구분자는 본문에 나타날 수 없는 문자열이어야 한다 — 치환 후 다시 쪼개기 때문이다.
    separator = "\n\n\x00SECTION\x00\n\n"
    combined = separator.join(
        [analysis.report_md or ""] + [s.get("body") or "" for s in sections]
    )
    substituted, references = _apply_footnotes(combined, papers)
    parts = (substituted or "").split(separator.strip("\n"))
    report_md = parts[0] if analysis.report_md is not None else None
    section_bodies = parts[1:]

    return (
        report_md,
        references,
        [
            {"name": s.get("name") or "", "body_md": body}
            for s, body in zip(sections, section_bodies)
        ],
    )
```

> ⚠ `separator`를 `\n\n\x00SECTION\x00\n\n`으로 만들고 `split`에서는 `strip("\n")`한
> 것에 주의한다. `_apply_footnotes`의 불릿 접기(`_FOOTNOTE_ONLY_BULLETS_RE`)가 줄 끝
> 개행을 소비할 수 있어, 구분자의 앞뒤 개행 수가 보존된다고 가정하면 안 된다.
> 가운데 `\x00SECTION\x00`만으로 쪼갠다.

- [ ] **Step 4: `_serialize`가 내려주게 한다**

같은 파일 `_serialize`에서:

```python
    report_md, references, sections = _footnoted_report(db, analysis)
```

반환 dict의 `"references": references,` 아래에 추가:

```python
        "sections": sections,
```

- [ ] **Step 5: 다른 호출부를 고친다**

`grep -n "_footnoted_report" backend/app/routers/public.py`로 나머지 호출부를 찾아
3-튜플 언패킹으로 고친다(분야 보고서 부록 `subfield_reports`가 이 함수를 쓴다).
부록에서는 세부 보고서를 쓰지 않으므로 `_`로 받는다.

- [ ] **Step 6: 테스트 통과를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add backend/app/routers/public.py backend/tests/test_api.py
git commit -m "feat(api): 세부 보고서를 각주 공유 상태로 내려준다

종합 보고서와 한 번에 치환해 번호 체계를 공유한다 — 따로 매기면 세부
보고서를 펼쳤을 때 [12]가 종합 보고서의 [12]와 다른 논문을 가리킨다."
```

---

### Task 5: 화면에 세부 보고서 토글

**Files:**
- Modify: `frontend/src/api.ts` (`Analysis` 타입)
- Modify: `frontend/src/pages/Report.tsx`
- Modify: `frontend/package.json` (`version` → minor 상향)

**Interfaces:**
- Consumes: Task 4의 `sections: [{name, body_md}]`
- Produces: 없음 (화면 종단)

`FieldReportPage.tsx`의 `withSub` 토글과 **같은 패턴**을 따른다 — URL 쿼리에 상태를 실어
북마크·공유가 되게 한다. 그 파일을 먼저 읽고 구현 형태를 맞출 것.

- [ ] **Step 1: 타입을 추가한다**

`frontend/src/api.ts`의 `export interface Analysis` **위**에 추가:

```ts
export interface ReportSection {
  name: string;
  body_md: string;
}
```

같은 파일 `Analysis` 인터페이스의 `references` 줄 아래에 추가:

```ts
  // 3단 reduce가 아닌 분석과 재생성 전 기존 분석은 빈 배열이다.
  sections?: ReportSection[];
```

- [ ] **Step 2: 토글과 본문을 붙인다**

`frontend/src/pages/Report.tsx`에서 `useSearchParams`를 쓰는 형태를
`FieldReportPage.tsx`와 맞춘다. 보고서 본문을 렌더하는 곳 **아래**에 추가:

```tsx
      {(data.sections?.length ?? 0) > 0 && (
        <section className="mt-10">
          <div className="mb-4 flex items-center gap-3">
            <h2 className="text-lg font-bold text-ink">세부 보고서</h2>
            <Switch
              checked={withSections}
              onChange={toggleSections}
              label="성과유형별 상세 포함"
            />
          </div>
          <p className="mb-4 text-sm text-muted">
            논문이 많아 성과유형별로 나눠 정리한 뒤 종합한 분석입니다. 종합 보고서는
            대표 성과 중심이라 개별 연구가 생략될 수 있어, 유형별 상세를 함께 보관합니다.
          </p>
          {withSections &&
            data.sections?.map((s) => (
              <article key={s.name} className="avoid-break mt-6 break-before-page">
                <h3 className="mb-2 text-base font-bold text-ink">{s.name}</h3>
                <Markdown>{s.body_md}</Markdown>
              </article>
            ))}
        </section>
      )}
```

`Switch`와 `Markdown`(또는 이 파일이 이미 쓰는 마크다운 렌더 컴포넌트)의 실제 이름과
import 경로는 `Report.tsx` 상단과 `FieldReportPage.tsx`를 읽어 그대로 맞춘다.

`withSections` / `toggleSections`는 `FieldReportPage.tsx:40-73`의 `withSub` 구현을
그대로 옮기되 쿼리 키만 `withSections`로 바꾼다.

- [ ] **Step 3: 버전을 올린다**

`frontend/package.json`의 `"version"`을 현재 값에서 minor 하나 올린다(예: `0.20.0` → `0.21.0`).

- [ ] **Step 4: 타입·린트·테스트를 통과시킨다**

```bash
cd frontend && npm run build && npm run lint && npm test
```
Expected: 셋 다 PASS. `npm run build`(tsc -b)만 타입 오류를 잡고,
`npm test`의 `spacing.test.ts`가 간격 위반을 잡는다(위 코드의 `mt-10`·`mb-4`·`gap-3`·
`mt-6`·`mb-2`는 모두 허용값 `10·4·3·6·2`).

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/api.ts frontend/src/pages/Report.tsx frontend/package.json
git commit -m "feat(frontend): 세부 보고서 토글

sections가 비면 토글 자체를 렌더하지 않는다 — 재생성 전 기존 분석 110건은
아무것도 바뀌지 않아야 한다."
```

---

### Task 6: 문서 갱신

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/2026-08-01-expansion-findings.md`

- [ ] **Step 1: `CLAUDE.md`의 파이프라인 표를 고친다**

5단계(reduce) 행의 비고에 다음을 덧붙인다:

```
건수가 `REDUCE_GROUP_THRESHOLD`(500) 넘으면 3단 reduce. 그룹별 중간 보고서는
버리지 않고 `analyses.sections_json`에 남긴다 — 최종 통합이 partial을 다시
압축하는 이중 압축이 500건 이상에서 인용률을 무너뜨리기 때문이다(실측 9.7% → 5.6%).
화면은 종합만 보여주고 토글로 펼친다.
```

- [ ] **Step 2: 로드맵의 진행 상태를 갱신한다**

`docs/2026-08-01-expansion-findings.md` §8 진행 상황 표에서 2단계 행을 고친다:

```
| 2단계 — 세부 보고서 보존 | ✅ **구현 완료** | 검증은 3단 reduce 분석 재생성 후 |
| 3단계 — Elsevier 초록 폴백 | ⬜ 미착수 | **다음 작업** · 약관 확인이 선행 조건 |
```

- [ ] **Step 3: 커밋**

```bash
git add CLAUDE.md docs/2026-08-01-expansion-findings.md
git commit -m "docs: 2단계 반영 — 세부 보고서 보존"
```

---

## 완료 조건

- `cd backend && ./.venv/bin/python -m pytest` 전체 통과
- `cd frontend && npm run build && npm run lint && npm test` 전부 통과
- `docker compose exec -T api alembic current` → `0016 (head)`
- 기존 분석 110건은 `sections_json`이 비어 있어 **화면이 이전과 동일**하다

## 검증 (배포 후)

3단 reduce로 가는 분석 하나를 재생성해 확인한다. 500건 초과 분석은
`SELECT id FROM analyses WHERE analyzed_count > 500 ORDER BY analyzed_count DESC LIMIT 1;`
로 찾는다(재생에너지 2025 = id 113이 유력).

1. 재생성 후 `sections_json`이 채워지는가
2. 화면에 토글이 나타나고 펼치면 유형별 상세가 보이는가
3. 세부 보고서의 각주 번호가 종합 보고서와 이어지는가(중복·충돌 없음)
4. 종합 보고서에 "정량 성과 정리" 표가 더 이상 없는가
5. 인용이 문장 안 괄호로 들어가는가(백틱·불릿 없음)

## 이 계획이 하지 않는 것

- **기존 110건 재생성** — 별도 판단. 재생성하면 `sections_json`이 채워지고 새 프롬프트가
  적용되지만 reduce LLM 비용이 다시 든다(약 $1.5).
- **3단 reduce 임계값(500) 조정** — partial을 보존하면 임계값이 낮을수록 세부가 촘촘해질
  뿐이다. 먼저 보존을 넣고 실제 보고서를 본 뒤 판단한다.
- **주제 기반 그룹화** — 임베딩이 필요해 "RAG를 쓰지 않는다" 방침과 충돌한다.
- **분야 보고서 부록에 세부 보고서 첨부** — 부록은 이미 세부기술 보고서를 붙이므로
  거기에 유형별 상세까지 넣으면 과하다.
