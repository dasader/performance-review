# 정량 지표 전수 집계 + 추출 통제 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 추출된 정량 지표를 코드로 전수 집계해 `stats_json`에 싣고, 지표명·단위가
집계 가능한 형태로 나오도록 추출 프롬프트를 통제한다.

**Architecture:** 수치는 이미 `paper_extractions.metrics_json`에 `{name, value, unit}`
구조로 저장돼 있으나 `stats.compute`가 집계하지 않아, LLM 보고서 표(논문 수와 무관하게
11~12행 고정)에만 실려 500건 이상에서 98.8%가 소실된다. 집계를 코드로 옮기고
(`stats.aggregate_metrics`), 지표명 오염(`Single-junction PSC PCE`)은 사후 정규화가 아니라
`MAP_INSTRUCTION` 규칙으로 막는다. `metrics_json`이 JSON 컬럼이라 새 `target` 필드가
그 안에 그대로 실려 **마이그레이션이 없다.**

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / pytest · React 19 + Vite + Tailwind / vitest

## Global Constraints

- 테스트는 반드시 `backend/.venv`를 쓴다: `cd backend && ./.venv/bin/python -m pytest`
- **`EXTRACTION_SCHEMA_VERSION`을 올리지 않는다.** 올리면 기존 22,059건이 전량 재추출된다
  (약 $6). 재추출은 사용자의 별도 승인 사항이며, 이 계획의 범위 밖이다.
- **마이그레이션을 만들지 않는다.** `target`은 기존 `metrics_json`(JSON 컬럼) 안에 실린다.
- 기존 `stats_json`에는 새 키가 없다(과거 분석 행). 프론트 타입은 전부 **선택 필드**로 두고
  화면은 값이 없으면 그 블록을 렌더하지 않는다.
- 프론트 레이아웃 간격은 4/8/12/16/24/40(Tailwind `1·2·3·4·6·10`)만 쓴다.
  `src/lib/spacing.test.ts`가 `.tsx` 전체를 훑어 고정한다.
- 넓은 표는 `.table-scroll`로 감싼다(`overflow-x-auto` 단독 금지).
- 프론트를 고치면 `frontend/package.json`의 `version`을 함께 올린다(기능 추가 → minor).

---

### Task 1: 지표 정규화·집계 함수

**Files:**
- Modify: `backend/app/services/stats.py`
- Test: `backend/tests/test_stats.py`

**Interfaces:**
- Consumes: `app.models.paper.PaperExtraction` (`metrics_json: list`)
- Produces: `stats.aggregate_metrics(extractions: list[PaperExtraction]) -> dict`
  반환 키: `metrics_total` `metrics_parsed` `metrics_papers` `metrics_unique` `top_metrics`.
  `top_metrics` 원소: `{"name": str, "unit": str, "count": int, "median": float, "p90": float, "max": float}`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_stats.py` 끝에 추가:

```python
def _e(key, metrics, subfield_id=1):
    return PaperExtraction(paper_key=key, subfield_id=subfield_id, tech_summary="x",
                           model_ver="m", metrics_json=metrics)


def test_metric_groups_merge_on_parenthetical_difference():
    """괄호 안 약어만 다른 같은 지표는 한 그룹으로 묶인다."""
    ext = [
        _e("a", [{"name": "전력 변환 효율 (PCE)", "value": "18.4", "unit": "%"}]),
        _e("b", [{"name": "전력 변환 효율", "value": "20.0", "unit": "%"}]),
        _e("c", [{"name": "전력  변환/효율", "value": "22.0", "unit": "%"}]),
    ]
    agg = stats.aggregate_metrics(ext)
    assert len(agg["top_metrics"]) == 1
    row = agg["top_metrics"][0]
    assert row["count"] == 3
    assert row["unit"] == "%"
    assert row["median"] == 20.0
    assert row["max"] == 22.0


def test_metric_groups_do_not_merge_across_units():
    """단위가 다르면 환산하지 않고 별도 그룹으로 둔다 — 잘못 합치면 1000배 오차가 난다."""
    ext = [
        _e("a", [{"name": "개방전압", "value": "1.2", "unit": "V"},
                 {"name": "개방전압", "value": "1.3", "unit": "V"}]),
        _e("b", [{"name": "개방전압", "value": "800", "unit": "mV"},
                 {"name": "개방전압", "value": "820", "unit": "mV"}]),
    ]
    agg = stats.aggregate_metrics(ext)
    units = {r["unit"] for r in agg["top_metrics"]}
    assert units == {"V", "mV"}


def test_metric_value_parsing_and_unparsed_are_counted_not_hidden():
    """숫자를 못 뽑은 값은 집계에서 빼되 metrics_total에는 남겨 분모를 속이지 않는다."""
    ext = [_e("a", [
        {"name": "효율", "value": "~14", "unit": "%"},
        {"name": "효율", "value": "1,200", "unit": "%"},
        {"name": "효율", "value": "측정 불가", "unit": "%"},
    ])]
    agg = stats.aggregate_metrics(ext)
    assert agg["metrics_total"] == 3
    assert agg["metrics_parsed"] == 2
    assert agg["top_metrics"][0]["count"] == 2
    assert agg["top_metrics"][0]["max"] == 1200.0


def test_single_occurrence_metrics_are_excluded_but_counted():
    """1회성 지표는 평균 낼 상대가 없어 표에서 빼되, 몇 종인지는 드러낸다."""
    ext = [_e("a", [
        {"name": "MED 프로세스 LCOW 증가율", "value": "17", "unit": "%"},
        {"name": "효율", "value": "10", "unit": "%"},
        {"name": "효율", "value": "20", "unit": "%"},
    ])]
    agg = stats.aggregate_metrics(ext)
    assert [r["name"] for r in agg["top_metrics"]] == ["효율"]
    assert agg["metrics_unique"] == 1


def test_metric_display_name_is_most_common_original():
    """표시 이름은 그룹에서 가장 많이 쓰인 원본 표기를 쓴다(소문자 키가 아니라)."""
    ext = [_e("a", [
        {"name": "전력변환효율(PCE)", "value": "1", "unit": "%"},
        {"name": "전력변환효율(PCE)", "value": "2", "unit": "%"},
        {"name": "Power Conversion Efficiency", "value": "3", "unit": "%"},
    ])]
    agg = stats.aggregate_metrics(ext)
    names = {r["name"] for r in agg["top_metrics"]}
    assert "전력변환효율(PCE)" in names


def test_metrics_papers_counts_papers_not_metrics():
    ext = [
        _e("a", [{"name": "효율", "value": "1", "unit": "%"},
                 {"name": "효율", "value": "2", "unit": "%"}]),
        _e("b", []),
    ]
    agg = stats.aggregate_metrics(ext)
    assert agg["metrics_papers"] == 1
    assert agg["metrics_total"] == 2


def test_aggregate_metrics_tolerates_malformed_rows():
    """LLM 출력이 스키마를 벗어나도 예외를 던지지 않는다."""
    ext = [_e("a", ["문자열", {"value": "1"}, {"name": "", "value": "2"}, None])]
    agg = stats.aggregate_metrics(ext)
    assert agg["top_metrics"] == []
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_stats.py -k metric -v`
Expected: FAIL — `AttributeError: module 'app.services.stats' has no attribute 'aggregate_metrics'`

- [ ] **Step 3: 최소 구현을 쓴다**

`backend/app/services/stats.py` — 상단 import를 다음으로 바꾼다:

```python
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime

from app.models.paper import Paper, PaperExtraction

TOP_N = 20
METRIC_TOP_N = 20
```

`_percentile`을 float도 받도록 넓히고(기존 호출부는 Step 5에서 `int()`로 감싼다):

```python
def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(int(len(ordered) * pct), len(ordered) - 1)
    return ordered[idx]
```

`_ranked` 아래에 추가:

```python
# 괄호 안에는 약어(PCE)나 조건(85°C)이 들어와 같은 지표를 쪼갠다 — 묶음 키에서는 떼어낸다.
_PAREN_RE = re.compile(r"\([^)]*\)")
_SEP_RE = re.compile(r"[\s_/·,]+")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _metric_key(name: str) -> str:
    """지표명을 묶음 키로 정규화한다. 표시에는 쓰지 않는다(원본 표기를 따로 보존)."""
    return _SEP_RE.sub(" ", _PAREN_RE.sub(" ", name)).strip().lower()


def _metric_value(raw: object) -> float | None:
    """값 문자열에서 첫 숫자를 뽑는다. '~14' '1,200' '18.43'을 처리하고,
    숫자가 없으면 None — 집계에서 빠지되 metrics_total에는 남는다."""
    text = raw if isinstance(raw, str) else str(raw or "")
    match = _NUM_RE.search(text.replace(",", ""))
    return float(match.group()) if match else None


def aggregate_metrics(extractions: list[PaperExtraction]) -> dict:
    """추출된 정량 지표를 (지표명, 단위)로 묶어 분포를 낸다.

    이 모듈 첫 줄의 원칙("통계는 전부 코드로 집계한다")을 metrics에도 적용하는 것이다.
    LLM 보고서의 정량 표는 논문 수와 무관하게 11~12행에서 포화하므로(실측), 수치를
    서술에 맡기면 500건 이상에서 98.8%가 소실된다.

    단위가 다르면 환산하지 않고 별도 그룹으로 둔다 — μA/cm2와 A/cm2를 잘못 합치면
    1000배 어긋나고, 그 오류는 표에서 드러나지 않는다.
    """
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    labels: dict[tuple[str, str], Counter] = defaultdict(Counter)
    total = parsed = papers = 0

    for extraction in extractions:
        metrics = extraction.metrics_json or []
        if metrics:
            papers += 1
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            total += 1
            name = (metric.get("name") or "").strip()
            key_name = _metric_key(name)
            value = _metric_value(metric.get("value"))
            if not key_name or value is None:
                continue
            parsed += 1
            key = (key_name, (metric.get("unit") or "").strip())
            values[key].append(value)
            labels[key][name] += 1

    top = [
        {
            "name": labels[key].most_common(1)[0][0],
            "unit": key[1],
            "count": len(nums),
            "median": round(statistics.median(nums), 4),
            "p90": round(_percentile(nums, 0.9), 4),
            "max": round(max(nums), 4),
        }
        for key, nums in values.items()
        if len(nums) > 1  # 1회성 지표는 분포가 없다 — metrics_unique로 존재만 남긴다.
    ]
    top.sort(key=lambda row: (-row["count"], row["name"]))

    return {
        "metrics_total": total,
        "metrics_parsed": parsed,
        "metrics_papers": papers,
        "metrics_unique": sum(1 for nums in values.values() if len(nums) == 1),
        "top_metrics": top[:METRIC_TOP_N],
    }
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_stats.py -k metric -v`
Expected: PASS (7건)

- [ ] **Step 5: 기존 인용수 p90이 int를 유지하는지 확인하고 감싼다**

`stats.compute`의 `citations` 블록에서 `p90` 줄을 바꾼다:

```python
            "p90": int(_percentile(citations, 0.9)),
```

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_stats.py -v`
Expected: PASS (기존 테스트 전부 포함)

- [ ] **Step 6: 커밋**

```bash
git add backend/app/services/stats.py backend/tests/test_stats.py
git commit -m "feat(stats): 정량 지표 (지표명, 단위) 그룹 집계 함수

보고서 표가 논문 수와 무관하게 11~12행에서 포화해 500건 이상에서 수치의
98.8%가 소실된다(실측). 집계를 코드로 옮긴다.

단위가 다르면 환산하지 않고 분리한다 — μA/cm2와 A/cm2를 합치면 1000배
오차가 나고 표에서 드러나지 않는다."
```

---

### Task 2: `stats.compute`에 지표 집계 싣기

**Files:**
- Modify: `backend/app/services/stats.py` (`compute` 반환 dict)
- Test: `backend/tests/test_stats.py`

**Interfaces:**
- Consumes: Task 1의 `aggregate_metrics(extractions) -> dict`
- Produces: `stats.compute(...)` 반환 dict에 `metrics_total` `metrics_parsed`
  `metrics_papers` `metrics_unique` `top_metrics` 5개 키 추가

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_stats.py` 끝에 추가:

```python
def test_compute_includes_metric_aggregate():
    papers = [_p("a"), _p("b")]
    ext = [
        PaperExtraction(paper_key="a", subfield_id=1, tech_summary="x", model_ver="m",
                        metrics_json=[{"name": "효율", "value": "10", "unit": "%"}]),
        PaperExtraction(paper_key="b", subfield_id=1, tech_summary="y", model_ver="m",
                        metrics_json=[{"name": "효율", "value": "30", "unit": "%"}]),
    ]
    s = stats.compute(papers, ext, snapshot_at=datetime(2026, 8, 1))
    assert s["metrics_total"] == 2
    assert s["metrics_papers"] == 2
    assert s["top_metrics"][0]["name"] == "효율"
    assert s["top_metrics"][0]["median"] == 20.0


def test_compute_with_no_metrics_still_returns_metric_keys():
    """지표가 하나도 없어도 키는 항상 존재해야 화면이 분기하지 않는다."""
    s = stats.compute([_p("a")], [], snapshot_at=datetime(2026, 8, 1))
    assert s["metrics_total"] == 0
    assert s["top_metrics"] == []
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_stats.py -k compute_ -v`
Expected: FAIL — `KeyError: 'metrics_total'`

- [ ] **Step 3: 최소 구현을 쓴다**

`stats.compute`의 반환 dict에서 `"by_achievement_type"` 줄 **바로 아래**에 추가:

```python
        **aggregate_metrics(extractions),
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: PASS (전체 스위트)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/services/stats.py backend/tests/test_stats.py
git commit -m "feat(stats): compute()에 정량 지표 집계 5개 키 추가

지표가 없어도 키는 항상 존재하게 해 화면이 분기하지 않도록 한다."
```

---

### Task 3: 추출 프롬프트·스키마 통제

**Files:**
- Modify: `backend/app/prompts.py` (`MAP_INSTRUCTION`, `MAP_SCHEMA`)
- Test: `backend/tests/test_mapper.py`

**Interfaces:**
- Consumes: 없음
- Produces: `MAP_SCHEMA["properties"]["metrics"]["items"]`에 `target` 필드(required),
  `MAP_SCHEMA["properties"]["achievement_type"]["enum"]`(9종).
  `mapper.EXTRACTION_SCHEMA_VERSION`은 **2 그대로 둔다.**

`target`은 새 DB 컬럼이 아니다 — `mapper.save_results`가
`row.metrics_json = item.get("metrics")`로 metrics 배열을 통째로 저장하고
`metrics_json`이 JSON 컬럼이라, 그 안에 자연히 실린다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_mapper.py` 끝에 추가:

```python
ACHIEVEMENT_TYPES = ["신소자", "신소재", "공정", "알고리즘", "아키텍처",
                     "성능향상", "시스템구현", "이론/해석", "기타"]


def test_map_schema_separates_metric_target_from_name():
    """측정 대상·조건이 지표명에 섞이면 같은 지표가 쪼개져 집계가 성립하지 않는다
    (실측: 재생에너지 2025에서 PCE가 7조각). target 필드로 분리한다."""
    item = MAP_SCHEMA["properties"]["metrics"]["items"]
    assert "target" in item["properties"]
    assert "target" in item["required"]


def test_map_schema_constrains_achievement_type_to_enum():
    """9종 지정인데 실제로는 17종이 저장돼 있었다(회로설계/회로 설계 등).
    이 값은 3단 reduce의 그룹 분할 키라 오염되면 그룹이 불필요하게 늘어난다."""
    assert MAP_SCHEMA["properties"]["achievement_type"]["enum"] == ACHIEVEMENT_TYPES


def test_map_instruction_states_metric_naming_rules():
    for phrase in ["물리량 이름만", "target", "ASCII"]:
        assert phrase in MAP_INSTRUCTION


def test_extraction_schema_version_not_bumped_without_approval():
    """이 값을 올리면 기존 22,059건이 전량 재추출된다(약 $6).
    재추출은 사용자의 별도 승인 사항이므로 여기서 조용히 오르지 않게 못박는다."""
    from app.services.mapper import EXTRACTION_SCHEMA_VERSION
    assert EXTRACTION_SCHEMA_VERSION == 2
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_mapper.py -k "map_schema or map_instruction or schema_version" -v`
Expected: FAIL — `KeyError: 'target'`

- [ ] **Step 3: 최소 구현을 쓴다**

`backend/app/prompts.py`의 `MAP_INSTRUCTION` 마지막 줄(`- 한국어로 작성하세요."""`)을
다음으로 바꾼다:

```python
- 한국어로 작성하세요.

metrics의 name·unit 작성 규칙 (코드가 통계로 집계하므로 반드시 지킬 것):
- name에는 **물리량 이름만** 씁니다. 측정 대상 물질·소자 구조·측정 조건을 name에
  넣지 마세요. 넣으면 같은 지표가 논문마다 다른 이름이 되어 집계가 불가능해집니다.
  나쁜 예: "Single-junction PSC PCE", "AlGaAs 밴드갭 에너지", "댐프히트 시험 후 효율 유지율"
  좋은 예: "전력변환효율(PCE)", "밴드갭", "효율 유지율"
- 널리 쓰이는 약어가 있으면 `한글명(약어)` 형태로 통일합니다.
  예: 전력변환효율(PCE), 개방전압(Voc), 단락전류밀도(Jsc), 충전율(FF), 에너지밀도
- 측정 대상·조건은 target 필드에 따로 씁니다. 없으면 빈 문자열("")로 두세요.
- unit은 ASCII로만 씁니다. 위첨자·유니코드 기호를 쓰지 말고 `/`와 숫자로 표기하세요.
  예: mA cm⁻², mA cm-2, mA/cm^2 → 전부 "mA/cm2" / Wh kg−1 → "Wh/kg"
- unit에 조건을 넣지 마세요. 나쁜 예: "% at 2 A cm-2", "% (1000시간 후)" → 그냥 "%"."""
```

같은 파일의 `MAP_SCHEMA`에서 `achievement_type`과 `metrics`를 바꾼다:

```python
        "achievement_type": {
            "type": "string",
            # 이 값은 reducer.group_for_reduce의 그룹 분할 키다. 자유 문자열로 두었더니
            # 9종 지정에 17종이 저장됐다(회로설계/회로 설계, 데이터셋/데이터셋 구축 등).
            "enum": ["신소자", "신소재", "공정", "알고리즘", "아키텍처",
                     "성능향상", "시스템구현", "이론/해석", "기타"],
        },
        "metrics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": "string"},
                    # 측정 대상·조건. name을 순수 물리량으로 유지하기 위한 배출구다 —
                    # 이 필드가 없으면 모델이 대상을 name에 도로 붙인다.
                    # 새 DB 컬럼이 아니다: metrics_json(JSON)에 그대로 실린다.
                    "target": {"type": "string"},
                },
                "required": ["name", "value", "target"],
            },
        },
```

- [ ] **Step 4: 테스트 통과를 확인한다**

Run: `cd backend && ./.venv/bin/python -m pytest -q`
Expected: PASS (전체 스위트 — `test_mapper.py`의 기존 스키마 비교 테스트 포함)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/prompts.py backend/tests/test_mapper.py
git commit -m "feat(prompts): 지표명·단위 규칙과 target 필드로 추출 단계에서 통제

사후 LLM 정규화는 품질은 좋으나(최대 그룹 5.2배) 분석당 \$0.198이고
캐시 히트가 6.3%라 회당 약 \$109다. 추출 통제는 map 비용 +14%(KR 연
+\$0.77)로 141배 싸다.

실측 A/B(논문 25편): 고유 지표명 56종→34종, 2회 이상 등장 지표에 속한
수치 15%→57%, 비ASCII 단위 6종→0종.

target은 metrics_json(JSON) 안에 실려 마이그레이션이 없다.
EXTRACTION_SCHEMA_VERSION은 올리지 않는다 — 재추출은 별도 승인 사항이고
테스트로 못박았다."
```

---

### Task 4: 화면에 정량 지표 분포 표

**Files:**
- Modify: `frontend/src/api.ts` (`Stats` 인터페이스)
- Modify: `frontend/src/components/StatsPanel.tsx`
- Modify: `frontend/package.json` (`version` 0.19.1 → 0.20.0)

**Interfaces:**
- Consumes: Task 2가 `stats_json`에 넣은 `top_metrics` 등 5개 키
- Produces: 없음 (화면 종단)

과거 분석 행의 `stats_json`에는 이 키들이 없다. **전부 선택 필드로 두고 값이 없으면
블록을 렌더하지 않는다.**

- [ ] **Step 1: 타입을 추가한다**

`frontend/src/api.ts`의 `export interface Stats` **위**에 추가:

```ts
export interface MetricStat {
  name: string;
  unit: string;
  count: number;
  median: number;
  p90: number;
  max: number;
}
```

같은 파일 `Stats` 인터페이스의 `by_achievement_type` 줄 아래에 추가:

```ts
  // 과거 분석의 stats_json에는 없다 — 반드시 선택 필드로 둔다.
  metrics_total?: number;
  metrics_parsed?: number;
  metrics_papers?: number;
  metrics_unique?: number;
  top_metrics?: MetricStat[];
```

- [ ] **Step 2: 표 컴포넌트를 추가한다**

`frontend/src/components/StatsPanel.tsx`의 3번째 줄을 바꾼다:

```tsx
import type { MetricStat, Stats } from "../api";
```

컴포넌트는 `if (!stats.searched_count) return null;`로 빈 통계를 이미 걸러내므로,
그 아래에서는 `stats.top_metrics` 접근이 타입상 안전하다(기존 `stats.by_year` 접근과 동일).

같은 파일 `function RankTable(` **바로 위**에 추가:

```tsx
function MetricTable({ rows, unique }: { rows: MetricStat[]; unique: number }) {
  if (!rows.length) return null;
  return (
    <div className="avoid-break table-scroll">
      <h3 className="mb-2 text-sm font-bold text-ink">정량 지표 분포</h3>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="tbl-head">
            <th className="py-2 pr-3 text-left">지표</th>
            <th className="py-2 pr-3 text-right">논문 수</th>
            <th className="py-2 pr-3 text-right">중앙값</th>
            <th className="py-2 pr-3 text-right">p90</th>
            <th className="py-2 text-right">최대</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={`${m.name}|${m.unit}`} className="border-b border-border-light">
              <td className="py-2 pr-3 text-ink-light">
                {m.name}
                {m.unit && <span className="text-muted"> ({m.unit})</span>}
              </td>
              <td className="py-2 pr-3 text-right text-xs tabular-nums text-muted">
                {m.count.toLocaleString()}
              </td>
              <td className="py-2 pr-3 text-right tabular-nums">{m.median.toLocaleString()}</td>
              <td className="py-2 pr-3 text-right tabular-nums">{m.p90.toLocaleString()}</td>
              <td className="py-2 text-right tabular-nums">{m.max.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-muted">
        여러 논문에 반복 등장한 지표만 싣습니다. 한 논문에만 나온 지표가{" "}
        {unique.toLocaleString()}종 더 있으며, 분포를 낼 수 없어 표에서 제외했습니다.
      </p>
    </div>
  );
}
```

- [ ] **Step 3: 표를 화면에 붙인다**

같은 파일에서 `<RankTable title="상위 기관" ...>`을 감싼 `<div className="grid gap-6 sm:grid-cols-2">`
**바로 위**에 추가:

```tsx
      <MetricTable rows={stats.top_metrics ?? []} unique={stats.metrics_unique ?? 0} />
```

- [ ] **Step 4: 버전을 올린다**

`frontend/package.json`의 `"version": "0.19.1"`을 `"version": "0.20.0"`으로 바꾼다.

- [ ] **Step 5: 타입·린트·테스트를 통과시킨다**

```bash
cd frontend && npm run build && npm run lint && npm test
```
Expected: 셋 다 PASS. `npm run build`(tsc -b)만이 타입 오류를 잡고,
`npm test`의 `spacing.test.ts`가 간격 위반을 잡는다.

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/api.ts frontend/src/components/StatsPanel.tsx frontend/package.json
git commit -m "feat(frontend): 정량 지표 분포 표

과거 분석의 stats_json에는 이 키들이 없으므로 전부 선택 필드로 두고
값이 없으면 렌더하지 않는다.

1회성 지표는 분포를 낼 수 없어 표에서 빼되 몇 종인지 각주로 밝힌다 —
분모를 숨기지 않는 기존 원칙(no_*_count)과 같다."
```

---

## 완료 조건

- `cd backend && ./.venv/bin/python -m pytest` 전체 통과
- `cd frontend && npm run build && npm run lint && npm test` 전부 통과
- `EXTRACTION_SCHEMA_VERSION == 2` (테스트가 고정)
- 마이그레이션 파일이 추가되지 않았다

## 이 계획이 하지 않는 것

- **전량 재추출** — `EXTRACTION_SCHEMA_VERSION` 상향은 사용자 별도 승인 후 별도 PR.
  승인 전까지 새 프롬프트는 **신규 논문 추출에만** 적용되고, 기존 22,059건의 오염된
  지표명은 그대로 남는다. 따라서 화면의 지표 표는 승인 전에는 대부분 비어 있는 것이 정상이다.
- `REDUCE_INSTRUCTION`의 정량 목표 교체, 세부 보고서 보존 — 2단계(별도 계획).
- 지표명 한/영 동의어 통합 — 추출 통제로 대체했다.
