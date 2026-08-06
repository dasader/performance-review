# 관리자 분야 탭 (3단계) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `분야 보고서`와 `전략기술로드맵` 탭을 **분야 보고서** 탭 하나로 합친다. 세부기술 탭과 같은 규약 — 셀을 체크해 고르고 `POST /admin/queue` 한 번으로 생성하며, 로드맵 원문은 그 분야 행을 펼쳐 편집한다. **탭 5개 → 4개.**

**Architecture:** 2단계가 만든 순수 함수층(`lib/selection.ts`)에 분야 산출물 두 종류를 더하고, `FieldTab.tsx`가 그것을 그린다. 백엔드는 두 가지만 손댄다 — 로드맵 판본·목표 수를 목록 응답에 싣고(지금은 `has_roadmap` 불리언뿐이라 화면이 판본을 못 보여준다), 1단계 리뷰가 남긴 `QueueComparisonIn.countries`의 `min_length=2` 숙제를 푼다.

**Tech Stack:** FastAPI · SQLAlchemy · Pydantic v2 · pytest / React 19 · TypeScript · Vite · Tailwind · vitest

## Global Constraints

- 백엔드 테스트: `cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest -q` (**워크트리에 `.venv`가 없다** — 메인 체크아웃의 절대 경로를 쓴다). 현재 **399 passed**.
- 프론트 게이트 3종 전부: `cd frontend && npm run build && npm run lint && npm test`. 현재 build ✓ · oxlint **0경고** · vitest **56/56**.
- **워크트리에 `node_modules`가 없다.** 링크: `cd frontend && ln -sfn /home/dev/code/performance-review/frontend/node_modules node_modules` · **커밋 전 삭제**: `rm -f frontend/node_modules`
- **레이아웃 간격은 6값뿐**(4/8/12/16/24/40 = Tailwind `1·2·3·4·6·10`). `src/lib/spacing.test.ts`가 `.tsx` 전체를 훑는다.
- **버튼·입력·표는 `src/index.css`의 계약을 쓴다**(`.btn*` · `.input` · `.tbl-head` · `.table-scroll`). 넓은 표는 반드시 `.table-scroll`.
- 401은 `e instanceof ApiError && e.status === 401` → `onUnauthorized()`.
- 커밋 메시지는 한국어로 쓰고 **왜**를 남긴다.
- `frontend/package.json` version을 올린다(`0.31.3` → `0.32.0`, 기능 추가).

## 2단계에서 확립된 규약 — 그대로 따른다

- **셀 하나 = 만들 수 있는 산출물 하나.** 체크해서 고르고 상단에서 한 번에 생성한다.
- **만들 수 없는 칸은 체크박스를 아예 안 그린다**(회색 처리만으로는 부족 — 열/행 전체선택에 딸려 들어간다).
- **판단은 `lib/`의 순수 함수로**, 컴포넌트는 그리기만 — 이 저장소에는 jsdom이 없어 렌더링을 자동 검증할 수 없다.
- **탭 이름은 그 탭에서 무엇을 하는지를 말한다**(2026-08-06 신고로 개명한 규칙).

## File Structure

| 파일 | 책임 |
|---|---|
| `backend/app/routers/admin.py` (수정) | `/field-reports`에 로드맵 판본·목표 수 · `QueueComparisonIn` 제약 완화 |
| `frontend/src/lib/selection.ts` (수정) | `Cell`을 판별 유니온으로 바꾸고 분야 두 종류 추가 |
| `frontend/src/api.ts` (수정) | `FieldReportRow` 타입 · 로드맵 CRUD 함수 |
| `frontend/src/components/FieldTab.tsx` (신규) | 분야 표 + 선택 + 생성 + 로드맵 펼침 편집 |
| `frontend/src/pages/Admin.tsx` (수정) | 탭 5개 → 4개 |
| 삭제 | `FieldReportsPanel.tsx` · `RoadmapEditor.tsx` |

---

### Task 1: `QueueComparisonIn`의 `min_length=2`를 푼다

**Files:**
- Modify: `backend/app/routers/admin.py` (`QueueComparisonIn`, `queue` 핸들러의 비교 루프)
- Test: `backend/tests/test_api.py`

**왜 지금인가:** 1단계 리뷰가 남긴 숙제다. 스키마에 `min_length=2`가 걸려 있어 **항목 하나가 잘못되면 요청 전체가 422로 죽는다** — "한 건이 막혀도 나머지는 큐잉한다"는 이 API의 존재 이유와 정면으로 충돌한다. 2단계 화면은 항상 2개 이상을 보내 드러나지 않았지만, 분야 탭이 같은 요청에 분야 산출물을 실어 보내기 시작하면 비교 항목 하나 때문에 분야 보고서까지 통째로 거부된다.

**Interfaces:**
- Consumes: `comparison.enqueue_comparison(db, subfield_id, year, countries)` — 국가 2개 미만이면 `ValueError`를 던진다(이미 그렇게 동작한다)
- Produces: `POST /admin/queue`가 국가 1개짜리 비교 항목을 422가 아니라 `skipped` 한 줄로 처리한다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_api.py`의 `# ── POST /admin/queue` 구역 끝에 추가한다.

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest tests/test_api.py -k "isolates" -v`
Expected: FAIL — 둘 다 **422**(Pydantic이 본문 전체를 거부). 200을 기대하는 단언에서 걸린다.

- [ ] **Step 3: 스키마 제약을 걷어낸다**

`QueueComparisonIn`을 고친다.

```python
class QueueComparisonIn(BaseModel):
    subfield_id: int
    # 다국 비교 하나만 만든다 — 1:1은 그 안의 섹션으로 조회된다(2026-08-04 설계).
    #
    # **min_length=2를 걸지 않는다.** 스키마에서 막으면 항목 하나가 잘못됐을 때
    # Pydantic이 요청 본문 전체를 422로 거부해, 같이 보낸 다른 종류까지 통째로
    # 사라진다 — "한 건이 막혀도 나머지는 큐잉한다"는 이 API의 존재 이유와 충돌한다.
    # 국가 수 검증은 enqueue_comparison이 ValueError로 하고, 핸들러가 그것을
    # skipped 한 줄로 옮긴다.
    countries: list[str] = []
```

핸들러의 비교 루프는 이미 `except (LookupError, ValueError)`로 감싸여 있으므로 **고칠 것이 없다** — `enqueue_comparison`이 국가 2개 미만에서 `ValueError("비교하려면 국가가 2개 이상이어야 합니다.")`를 던지고 그것이 그대로 사유가 된다. 확인만 하고 지나간다.

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest -q`
Expected: 401 passed (399 + 2).

- [ ] **Step 5: 커밋**

```bash
git add backend/app/routers/admin.py backend/tests/test_api.py
git commit -m "fix: 비교 항목 하나가 요청 전체를 422로 죽이지 않게

1단계 리뷰가 남긴 숙제. QueueComparisonIn.countries의 min_length=2는 스키마 단계
거부라, 항목 하나가 잘못되면 같은 요청에 실린 다른 종류까지 통째로 사라진다 —
'한 건이 막혀도 나머지는 큐잉한다'는 이 API의 존재 이유와 충돌한다.

2단계 화면은 항상 2개 이상을 보내 드러나지 않았지만, 분야 탭이 같은 요청에 분야
산출물을 실으면 비교 하나 때문에 분야 보고서까지 거부된다.

국가 수 검증은 enqueue_comparison의 ValueError가 이미 하고 있고, 핸들러가 그것을
skipped 사유로 옮긴다."
```

---

### Task 2: `/field-reports`에 로드맵 판본·목표 수

**Files:**
- Modify: `backend/app/routers/admin.py` (`field_reports_overview`)
- Test: `backend/tests/test_api.py`

**왜:** 지금 응답은 `has_roadmap` 불리언뿐이라, 분야 탭의 로드맵 열이 "등록됨/미등록"밖에 못 쓴다. 설계가 정한 화면은 `v3.1 · 65목표`를 보여준다 — 어느 판본으로 점검했는지가 보고서의 신뢰도를 좌우하므로 목록에서 바로 보여야 한다. 분야마다 `/fields/{id}/roadmap`을 따로 부르면 10번 나간다.

**Interfaces:**
- Consumes: `reducer.count_goal_rows(content_md) -> int`
- Produces: `/admin/field-reports` 각 row에 `roadmap: {version_label: str, goal_count: int} | null`. Task 4의 화면이 쓴다. 기존 `has_roadmap`은 **그대로 둔다**(다른 소비자가 없더라도 이번 범위 밖의 제거는 하지 않는다 — 4단계에서 정리).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_api.py`에서 기존 `field-reports` 테스트 근처에 추가한다. `_put_roadmap` 헬퍼가 이미 있으면 그것을 쓰고, 없으면 아래처럼 직접 PUT한다.

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest tests/test_api.py -k "roadmap_version or unregistered" -v`
Expected: FAIL — `KeyError: 'roadmap'`.

- [ ] **Step 3: 응답에 로드맵을 싣는다**

`field_reports_overview`에서 `roadmap_fields`를 만드는 줄을 바꾼다.

```python
    # 판본과 목표 수까지 함께 읽는다 — 분야마다 /fields/{id}/roadmap을 따로 부르면
    # 10번 나가고, 목록에서 "어느 판본으로 점검했는가"를 못 보여준다.
    roadmaps = {
        r.field_id: {
            "version_label": r.version_label,
            "goal_count": reducer.count_goal_rows(r.content_md),
        }
        for r in db.query(Roadmap.field_id, Roadmap.version_label, Roadmap.content_md)
    }
```

`rows.append({...})`에서 `has_roadmap`은 그대로 두고 한 줄을 더한다.

```python
            "has_roadmap": field.id in roadmaps,
            "roadmap": roadmaps.get(field.id),
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest -q`
Expected: 403 passed (401 + 2).

- [ ] **Step 5: 커밋**

```bash
git add backend/app/routers/admin.py backend/tests/test_api.py
git commit -m "feat: 분야 보고서 목록에 로드맵 판본·목표 수

어느 판본으로 점검했는지가 보고서 신뢰도를 좌우하므로 목록에서 바로 보여야 한다.
분야마다 /fields/{id}/roadmap을 따로 부르면 10번 나간다.

미등록은 null로 낸다 — 빈 문자열로 내면 '등록됐는데 판본명이 비었다'와 구별되지 않는다."
```

---

### Task 3: `Cell`을 판별 유니온으로 바꾸고 분야 두 종류를 더한다

**Files:**
- Modify: `frontend/src/lib/selection.ts`
- Test: `frontend/src/lib/selection.test.ts`

**왜 유니온인가:** 지금 `Cell`은 `{kind, subfieldId, country?}`라 분야 산출물을 넣으려면 `fieldId?`까지 선택 필드가 되어 어느 조합이 유효한지 타입이 말하지 못한다. 판별 유니온으로 바꾸면 TS가 `kind`로 좁혀 주고, `toQueuePayload`의 `cell.country as string` 단언(2단계 리뷰가 Minor로 남긴 것)도 함께 사라진다.

**Interfaces:**
- Consumes: 없음 (순수 함수)
- Produces: `Cell` 유니온 4종, `cellKey`/`parseCellKey` 확장, `toQueuePayload`가 `field_reports`·`roadmap_checks`를 채운다. Task 5의 `FieldTab`이 쓴다. **기존 `rowCells`·`headerState`·`toggleAll`·`hasPendingWork`의 시그니처는 바뀌지 않는다** — `SubfieldTab`은 손대지 않는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/lib/selection.test.ts` 끝에 추가하고, import 줄에 필요한 이름을 더한다.

```ts
describe("분야 산출물 셀", () => {
  it("분야 종합과 로드맵 점검은 분야 하나로 식별된다", () => {
    expect(cellKey({ kind: "field_report", fieldId: 4 })).toBe("field_report:4");
    expect(cellKey({ kind: "roadmap_check", fieldId: 4 })).toBe("roadmap_check:4");
  });

  it("같은 분야라도 종류가 다르면 다른 셀이다", () => {
    expect(cellKey({ kind: "field_report", fieldId: 4 })).not.toBe(
      cellKey({ kind: "roadmap_check", fieldId: 4 }),
    );
  });

  it("왕복 변환이 원본과 같다", () => {
    for (const cell of [
      { kind: "field_report", fieldId: 4 },
      { kind: "roadmap_check", fieldId: 9 },
    ] as const) {
      expect(parseCellKey(cellKey(cell))).toEqual(cell);
    }
  });

  it("요청 본문의 분야 목록에 담긴다 — 분야 탭은 분석·비교를 만들지 않는다", () => {
    const selected = new Set(["field_report:4", "roadmap_check:4", "roadmap_check:9"]);
    expect(toQueuePayload(selected, { year: 2026, countries: ["KR"], force: false })).toEqual({
      year: 2026,
      analyses: [],
      comparisons: [],
      field_reports: [4],
      roadmap_checks: [4, 9],
    });
  });

  it("세부기술 셀과 분야 셀을 섞어 보내도 각자 자리로 간다", () => {
    const selected = new Set(["analysis:3:US", "field_report:4"]);
    const body = toQueuePayload(selected, { year: 2026, countries: ["KR", "US"], force: false });
    expect(body.analyses).toEqual([{ subfield_id: 3, country: "US", force: false }]);
    expect(body.field_reports).toEqual([4]);
  });
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/lib/selection.test.ts`
Expected: FAIL — `field_report` 키가 `comparison` 분기로 떨어져 `cellKey`가 `"comparison:undefined"` 비슷한 값을 낸다.

- [ ] **Step 3: 타입과 함수를 고친다**

`frontend/src/lib/selection.ts`에서 `CellKind`·`Cell`·`cellKey`·`parseCellKey`를 바꾼다.

```ts
export type CellKind = "analysis" | "comparison" | "field_report" | "roadmap_check";

// 화면의 셀 하나 = 만들 수 있는 산출물 하나.
//
// 판별 유니온으로 두는 이유: 종류마다 필요한 식별자가 다르다(세부기술 × 국가 /
// 세부기술 / 분야). 전부 선택 필드로 두면 어느 조합이 유효한지 타입이 말하지 못하고,
// 값을 꺼낼 때마다 단언이 붙는다.
//
// 연도는 화면 전역 필터라 키에 넣지 않는다 — 연도를 바꾸면 선택을 비운다.
export type Cell =
  | { kind: "analysis"; subfieldId: number; country: string }
  | { kind: "comparison"; subfieldId: number }
  | { kind: "field_report"; fieldId: number }
  | { kind: "roadmap_check"; fieldId: number };

export function cellKey(cell: Cell): string {
  switch (cell.kind) {
    case "analysis":
      return `analysis:${cell.subfieldId}:${cell.country}`;
    case "comparison":
      return `comparison:${cell.subfieldId}`;
    default:
      return `${cell.kind}:${cell.fieldId}`;
  }
}

export function parseCellKey(key: string): Cell {
  const [kind, id, country] = key.split(":");
  switch (kind) {
    case "analysis":
      return { kind: "analysis", subfieldId: Number(id), country };
    case "comparison":
      return { kind: "comparison", subfieldId: Number(id) };
    case "roadmap_check":
      return { kind: "roadmap_check", fieldId: Number(id) };
    default:
      return { kind: "field_report", fieldId: Number(id) };
  }
}
```

`toQueuePayload`의 루프를 바꾼다. **`as string` 단언이 사라지는 것이 이 변경의 부수 효과다.**

```ts
  for (const key of [...selected].sort()) {
    const cell = parseCellKey(key);
    switch (cell.kind) {
      case "analysis":
        body.analyses.push({
          subfield_id: cell.subfieldId,
          country: cell.country,
          force: opts.force,
        });
        break;
      case "comparison":
        body.comparisons.push({ subfield_id: cell.subfieldId, countries: opts.countries });
        break;
      case "field_report":
        body.field_reports.push(cell.fieldId);
        break;
      case "roadmap_check":
        body.roadmap_checks.push(cell.fieldId);
        break;
    }
  }
```

`rowCells`가 만드는 셀에 타입 주석이 붙어 있으면 유니온에 맞게 고친다(`Cell[]` 그대로면 추론이 통한다).

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npm run build && npm test`
Expected: build 성공(`SubfieldTab.tsx`가 같은 타입을 쓰므로 여기서 어긋남이 잡힌다) · vitest 61/61.

- [ ] **Step 5: 커밋**

```bash
rm -f frontend/node_modules
git add frontend/src/lib/selection.ts frontend/src/lib/selection.test.ts
git commit -m "feat: 셀 타입을 판별 유니온으로 — 분야 산출물 두 종류 추가

종류마다 필요한 식별자가 다른데(세부기술 × 국가 / 세부기술 / 분야) 전부 선택 필드로
두면 어느 조합이 유효한지 타입이 말하지 못한다. 유니온으로 바꾸니 toQueuePayload의
cell.country as string 단언도 함께 사라졌다 — 2단계 리뷰가 Minor로 남긴 것이다."
```

---

### Task 4: `api.ts` — 분야 목록 타입과 로드맵 CRUD

**Files:**
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Produces: `interface FieldReportCell`, `interface FieldReportRow`, `interface FieldReportsResponse`, `interface RoadmapDoc`, `getRoadmap(fieldId, adminKey)`, `putRoadmap(fieldId, body, adminKey)`, `deleteRoadmap(fieldId, adminKey)`. Task 5가 전부 쓴다.

- [ ] **Step 1: 타입과 함수를 추가한다**

`api.ts`의 `QueueResponse` 아래에 넣는다. 필드명은 백엔드 `field_reports_overview`와 `get_roadmap`에서 확인한 것이다 — **작성 전에 `backend/app/routers/admin.py`의 두 핸들러를 직접 읽어 대조할 것.**

```ts
// ── 분야 보고서 탭 ──

export interface FieldReportCell {
  status: "pending" | "done" | "failed";
  source_count: number;
  generated_at: string | null;
  error: string | null;
}

export interface FieldReportRow {
  field_id: number;
  field_name: string;
  has_roadmap: boolean;
  // 등록된 로드맵의 판본과 목표 수. 미등록이면 null — 빈 문자열로 내면
  // "등록됐는데 판본명이 비었다"와 구별되지 않는다.
  roadmap: { version_label: string; goal_count: number } | null;
  report: FieldReportCell | null;
  roadmap_check: FieldReportCell | null;
}

export interface FieldReportsResponse {
  year: number;
  rows: FieldReportRow[];
}

// 로드맵 원문. 미등록 분야도 404가 아니라 빈 값이 온다 — 편집 폼이 그대로 새 입력을 받는다.
export interface RoadmapDoc {
  version_label: string;
  content_md: string;
  goal_count: number;
  updated_at: string | null;
}

export function getRoadmap(fieldId: number, adminKey: string) {
  return get<RoadmapDoc>(`/admin/fields/${fieldId}/roadmap`, adminKey);
}

export function putRoadmap(
  fieldId: number,
  body: { version_label: string; content_md: string },
  adminKey: string,
) {
  return put<{ goal_count: number }>(`/admin/fields/${fieldId}/roadmap`, body, adminKey);
}

export function deleteRoadmap(fieldId: number, adminKey: string) {
  return del(`/admin/fields/${fieldId}/roadmap`, adminKey);
}
```

`get`·`post`·`put`·`del`은 이 파일 68~94행에 이미 있다(확인함). 새로 만들지 말고 그대로 쓴다 — `put<T>(path, body, adminKey)`, `del(path, adminKey): Promise<void>`.

- [ ] **Step 2: 타입 검사를 통과시킨다**

Run: `cd frontend && ln -sfn /home/dev/code/performance-review/frontend/node_modules node_modules && npm run build && npm run lint`
Expected: 성공, lint 0경고.

- [ ] **Step 3: 커밋**

```bash
rm -f frontend/node_modules
git add frontend/src/api.ts
git commit -m "feat: 분야 보고서 목록 타입과 로드맵 CRUD 함수

로드맵은 미등록이어도 404가 아니라 빈 값이 온다 — 편집 폼이 그대로 새 입력을 받는
백엔드 규약이라 타입도 nullable이 아니라 빈 문자열을 기대한다."
```

---

### Task 5: `FieldTab.tsx` — 표·선택·생성·로드맵 펼침

**Files:**
- Create: `frontend/src/components/FieldTab.tsx`

**Interfaces:**
- Consumes: Task 3의 `cellKey`·`headerState`·`toggleAll`·`toQueuePayload`, Task 4의 타입·함수, 그리고 기존 `ApiError`·`get`·`queueAll`·`STATUS_LABEL`·`estimateCost`·`usePolling`·`YearInput`·`StatusBadge`·`formatGeneratedAt`
- Produces: `export default function FieldTab({adminKey, onUnauthorized}: {adminKey: string; onUnauthorized: () => void})`

**설계 결정:**
- **종합보고서 칸은 항상 선택 가능**하다 — 세부기술 보고서가 하나도 없으면 서버가 `skipped` 사유로 알려 준다. 화면이 미리 판단하려면 세부기술 보고서 현황을 또 읽어야 하는데, 그 값은 이 응답에 없다.
- **로드맵 점검 칸은 로드맵이 없으면 선택 불가**(`대상 아님`). 이건 이 응답의 `roadmap`으로 바로 알 수 있고, 바로 옆 칸에 `[등록]`이 있어 해결 수단이 같은 줄에 있다.
- **로드맵 편집기는 펼치기 전에 그리지 않는다**(13KB 텍스트영역이 표를 밀어낸다). 펼칠 때 `getRoadmap`으로 원문을 가져온다 — 목록 응답에 원문까지 실으면 10개 분야 × 13KB를 매번 읽는다.

- [ ] **Step 1: 컴포넌트를 만든다**

`frontend/src/components/FieldTab.tsx`를 새로 만든다.

```tsx
import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  STATUS_LABEL,
  deleteRoadmap,
  get,
  getRoadmap,
  putRoadmap,
  queueAll,
  type FieldReportCell,
  type FieldReportRow,
  type FieldReportsResponse,
  type QueueResponse,
} from "../api";
import { estimateCost } from "../lib/cost";
import { formatGeneratedAt } from "../lib/format";
import { usePolling } from "../lib/hooks";
import { cellKey, headerState, toQueuePayload, toggleAll } from "../lib/selection";
import StatusBadge from "./StatusBadge";
import YearInput from "./YearInput";

// 관리자 "분야 보고서" 탭 — 분야 종합·로드맵 점검 현황과 생성, 로드맵 원문 편집이
// 한 화면에 있다. 세부기술 탭과 같은 규약(체크해서 고르고 위에서 한 번에 생성).
//
// 로드맵을 여기 둔 이유: 로드맵은 분야의 속성이고 점검 보고서의 입력이다.
// "점검이 안 돌아가네" → "로드맵이 미등록이구나"가 화면 이동 없이 이어져야 한다.
export default function FieldTab({
  adminKey,
  onUnauthorized,
}: {
  adminKey: string;
  onUnauthorized: () => void;
}) {
  const [year, setYear] = useState(new Date().getFullYear());
  const [data, setData] = useState<FieldReportsResponse | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [openField, setOpenField] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QueueResponse | null>(null);

  const load = useCallback(() => {
    get<FieldReportsResponse>(`/admin/field-reports?year=${year}`, adminKey)
      .then(setData)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) return onUnauthorized();
        setError(e instanceof Error ? e.message : "현황을 불러오지 못했습니다.");
      });
  }, [year, adminKey, onUnauthorized]);

  useEffect(load, [load]);

  // 연도를 바꾸면 선택과 결과를 비운다 — 다른 연도 대상이 남으면 잘못 큐잉된다.
  useEffect(() => {
    setSelected(new Set());
    setResult(null);
  }, [year]);

  const rows = data?.rows ?? [];

  // 로드맵 점검은 로드맵이 있어야 만들 수 있다. 종합은 세부기술 보고서가 있어야 하지만
  // 그 현황이 이 응답에 없으므로 서버의 skipped 사유에 맡긴다.
  const cellsOf = (row: FieldReportRow) => {
    const keys = [cellKey({ kind: "field_report", fieldId: row.field_id })];
    if (row.roadmap) keys.push(cellKey({ kind: "roadmap_check", fieldId: row.field_id }));
    return keys;
  };
  const allCandidates = rows.flatMap(cellsOf);

  const hasPending = rows.some(
    (r) => r.report?.status === "pending" || r.roadmap_check?.status === "pending",
  );
  usePolling(hasPending, load);

  // 갱신으로 사라진 대상은 선택에서 조용히 뺀다.
  useEffect(() => {
    setSelected((prev) => {
      const valid = new Set(allCandidates);
      const next = new Set([...prev].filter((k) => valid.has(k)));
      return next.size === prev.size ? prev : next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows]);

  const payload = toQueuePayload(selected, { year, countries: [], force: false });
  const cost = estimateCost(payload, {});
  const total = payload.field_reports.length + payload.roadmap_checks.length;

  const toggle = (key: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const runQueue = async () => {
    if (total === 0) return;
    if (
      !confirm(
        `${year}년 분야 종합보고서 ${payload.field_reports.length}건, ` +
          `로드맵 점검 ${payload.roadmap_checks.length}건을 생성합니다.\n` +
          "LLM 호출 비용이 발생합니다. 계속할까요?",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setResult(await queueAll(payload, adminKey));
      setSelected(new Set());
      load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      setError(e instanceof Error ? e.message : "생성 요청에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const columnCandidates = (kind: "field_report" | "roadmap_check") =>
    rows
      .map((row) => cellKey({ kind, fieldId: row.field_id }))
      .filter((k) => allCandidates.includes(k));

  return (
    <section className="mt-6 border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-accent">분야 종합·로드맵 점검 현황</h2>
        <YearInput year={year} onChange={setYear} />
      </div>

      <p className="mt-2 text-xs text-muted">
        <strong className="text-ink">종합보고서 칸</strong>은 그 분야 세부기술 보고서를 합성한
        보고서, <strong className="text-ink">로드맵 점검 칸</strong>은 그 보고서로 로드맵 목표를
        전수 대조한 결과입니다. 만들 것을 체크해서 고르고 위에서 한 번에 생성합니다.
        <strong className="text-ink"> 대상 아님</strong>은 로드맵이 등록되지 않아 점검을 만들 수
        없는 칸입니다 — 오른쪽 로드맵 열에서 등록할 수 있습니다.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-3 border border-border-light bg-paper p-3">
        <span className="text-sm text-ink">
          종합 {payload.field_reports.length}건 · 점검 {payload.roadmap_checks.length}건 선택됨
        </span>
        {cost.reportUsd > 0 && (
          <span className="text-xs text-muted">예상 ${cost.reportUsd.toFixed(2)}</span>
        )}
        <button
          type="button"
          onClick={runQueue}
          disabled={busy || total === 0}
          className="btn btn-primary btn-sm"
        >
          {busy
            ? "요청 중…"
            : `종합 ${payload.field_reports.length} · 점검 ${payload.roadmap_checks.length}건 생성`}
        </button>
        {selected.size > 0 && (
          <button type="button" onClick={() => setSelected(new Set())} className="btn btn-neutral btn-sm">
            선택 해제
          </button>
        )}
      </div>

      {error && <p className="mt-3 text-sm text-danger">{error}</p>}

      {/* 부분 실패를 사유와 함께 보여준다 — 조용히 건너뛰지 않는 것이 이 API의 요점이다. */}
      {result && (
        <div className="mt-3 border border-border-light bg-paper p-3 text-sm">
          <p className="text-ink">
            종합 {result.queued.field_reports}건 · 점검 {result.queued.roadmap_checks}건 큐잉됨
          </p>
          {result.skipped.length > 0 && (
            <ul className="mt-2 space-y-1">
              {result.skipped.map((s, i) => (
                <li key={i} className="text-xs text-muted">
                  {rows.find((r) => r.field_id === s.field_id)?.field_name ?? s.field_id} — {s.reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {data && (
        <div className="mt-6 table-scroll border-t border-border">
          <table className="w-full border-collapse text-sm">
            <thead className="tbl-head">
              <tr className="border-b border-border">
                <th>분야</th>
                {(["field_report", "roadmap_check"] as const).map((kind) => {
                  const candidates = columnCandidates(kind);
                  const state = headerState(selected, candidates);
                  return (
                    <th key={kind}>
                      <label className="flex items-center justify-center gap-1">
                        <input
                          type="checkbox"
                          checked={state === "all"}
                          ref={(el) => {
                            if (el) el.indeterminate = state === "some";
                          }}
                          onChange={() =>
                            setSelected((prev) => toggleAll(prev, candidates, state !== "all"))
                          }
                        />
                        {kind === "field_report" ? "종합보고서" : "로드맵 점검"}
                      </label>
                    </th>
                  );
                })}
                <th>로드맵</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <FieldRow
                  key={row.field_id}
                  row={row}
                  selected={selected}
                  open={openField === row.field_id}
                  onToggleCell={toggle}
                  onToggleOpen={() =>
                    setOpenField((cur) => (cur === row.field_id ? null : row.field_id))
                  }
                  adminKey={adminKey}
                  onUnauthorized={onUnauthorized}
                  onSaved={load}
                  onError={setError}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ReportCell({
  cell,
  cellKeyStr,
  selectable,
  selected,
  onToggle,
}: {
  cell: FieldReportCell | null;
  cellKeyStr: string;
  selectable: boolean;
  selected: Set<string>;
  onToggle: (key: string) => void;
}) {
  if (!selectable) {
    // 로드맵 미등록 — 체크박스를 아예 안 그린다(회색 처리만으로는 열 전체선택에 딸려 온다).
    return (
      <span className="text-xs text-faint" title="로드맵이 등록되지 않아 점검을 만들 수 없습니다">
        대상 아님
      </span>
    );
  }
  return (
    <>
      <label className="inline-flex items-center gap-1">
        <input type="checkbox" checked={selected.has(cellKeyStr)} onChange={() => onToggle(cellKeyStr)} />
        {cell ? (
          <StatusBadge status={cell.status} label={STATUS_LABEL[cell.status] ?? cell.status} />
        ) : (
          <span className="text-xs text-muted">미생성</span>
        )}
      </label>
      {cell?.status === "done" && cell.generated_at && (
        <p className="text-xs text-muted">{formatGeneratedAt(cell.generated_at)}</p>
      )}
      {cell?.status === "failed" && cell.error && (
        <p className="max-w-xs text-xs text-danger">{cell.error}</p>
      )}
    </>
  );
}

// 분야 한 행 + 펼쳤을 때의 로드맵 편집기. 펼치기 전에는 원문을 가져오지도 그리지도
// 않는다 — 10개 분야 × 13KB를 목록 응답에 실을 이유가 없고, 텍스트영역이 표를 밀어낸다.
function FieldRow({
  row, selected, open, onToggleCell, onToggleOpen, adminKey, onUnauthorized, onSaved, onError,
}: {
  row: FieldReportRow;
  selected: Set<string>;
  open: boolean;
  onToggleCell: (key: string) => void;
  onToggleOpen: () => void;
  adminKey: string;
  onUnauthorized: () => void;
  onSaved: () => void;
  onError: (message: string) => void;
}) {
  return (
    <>
      <tr className="border-b border-border-light">
        <td className="py-3 pr-3 font-medium text-ink">{row.field_name}</td>
        <td className="py-3 pr-3 text-center">
          <ReportCell
            cell={row.report}
            cellKeyStr={cellKey({ kind: "field_report", fieldId: row.field_id })}
            selectable
            selected={selected}
            onToggle={onToggleCell}
          />
        </td>
        <td className="py-3 pr-3 text-center">
          <ReportCell
            cell={row.roadmap_check}
            cellKeyStr={cellKey({ kind: "roadmap_check", fieldId: row.field_id })}
            selectable={row.roadmap !== null}
            selected={selected}
            onToggle={onToggleCell}
          />
        </td>
        <td className="py-3 whitespace-nowrap">
          <span className="text-xs text-muted">
            {row.roadmap
              ? `${row.roadmap.version_label} · 목표 ${row.roadmap.goal_count}개`
              : "미등록"}
          </span>
          <button type="button" onClick={onToggleOpen} className="ml-2 btn btn-neutral btn-sm">
            {open ? "닫기" : row.roadmap ? "편집" : "등록"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="border-b border-border-light bg-sunken">
          <td colSpan={4} className="py-3 pr-3">
            <RoadmapForm
              fieldId={row.field_id}
              adminKey={adminKey}
              onUnauthorized={onUnauthorized}
              onSaved={onSaved}
              onError={onError}
            />
          </td>
        </tr>
      )}
    </>
  );
}

function RoadmapForm({
  fieldId, adminKey, onUnauthorized, onSaved, onError,
}: {
  fieldId: number;
  adminKey: string;
  onUnauthorized: () => void;
  onSaved: () => void;
  onError: (message: string) => void;
}) {
  const [version, setVersion] = useState("");
  const [content, setContent] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getRoadmap(fieldId, adminKey)
      .then((doc) => {
        setVersion(doc.version_label);
        setContent(doc.content_md);
        setLoaded(true);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) return onUnauthorized();
        onError(e instanceof Error ? e.message : "로드맵을 불러오지 못했습니다.");
      });
  }, [fieldId, adminKey, onUnauthorized, onError]);

  const save = async () => {
    setSaving(true);
    try {
      await putRoadmap(fieldId, { version_label: version, content_md: content }, adminKey);
      onSaved();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      onError(e instanceof Error ? e.message : "저장에 실패했습니다.");
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!confirm("등록된 로드맵을 삭제할까요? 이미 생성된 점검 보고서는 남습니다.")) return;
    try {
      await deleteRoadmap(fieldId, adminKey);
      setVersion("");
      setContent("");
      onSaved();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      onError(e instanceof Error ? e.message : "삭제에 실패했습니다.");
    }
  };

  if (!loaded) return <p className="text-xs text-muted">불러오는 중…</p>;

  return (
    <div className="space-y-3">
      {/* 비공개 판본 여부는 관리자만 판단할 수 있다 — 어디로 나가는지 명시한다.
          임베딩을 로컬화해도 이 문제는 해결되지 않는다(최종 생성이 외부 모델이면
          원문은 프롬프트로 나간다). */}
      <p className="banner banner-warn text-xs">
        ⚠ 여기 저장한 원문은 점검 보고서를 생성할 때 <strong>Gemini API로 전송</strong>됩니다.
        외부로 내보낼 수 없는 판본인지 확인한 뒤 입력하세요.
      </p>

      <label className="block max-w-md text-sm">
        <span className="text-muted">판본</span>
        <input
          value={version}
          onChange={(e) => setVersion(e.target.value)}
          placeholder="2026 제1호 개정"
          className="input mt-1"
        />
      </label>

      <label className="block text-sm">
        <span className="text-muted">
          원문 (마크다운) — 단계별 목표는{" "}
          <code className="bg-sunken px-1 font-sans text-ink">| 단계 | 시기 | 기술적 목표 |</code>{" "}
          형태의 표로 넣습니다. 표가 아니면 저장이 거부됩니다.
        </span>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={12}
          className="input mt-1 w-full"
        />
      </label>

      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={save} disabled={saving} className="btn btn-primary btn-sm">
          {saving ? "저장 중…" : "저장"}
        </button>
        <button type="button" onClick={remove} className="btn btn-danger-quiet btn-sm">
          삭제
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 게이트를 전부 돌린다**

```bash
cd frontend && ln -sfn /home/dev/code/performance-review/frontend/node_modules node_modules
npm run build && npm run lint && npm test
```
Expected: 전부 통과, lint 0경고. 간격 검사가 깨지면 6값(1·2·3·4·6·10) 밖의 클래스를 쓴 것이니 그 클래스를 고친다.

- [ ] **Step 3: 커밋**

```bash
rm -f frontend/node_modules
git add frontend/src/components/FieldTab.tsx
git commit -m "feat: 분야 탭 — 종합·점검 현황과 생성, 로드맵 편집을 한 화면에

로드맵을 여기 둔 이유: 로드맵은 분야의 속성이고 점검 보고서의 입력이다.
'점검이 안 돌아가네' → '로드맵이 미등록이구나'가 화면 이동 없이 이어져야 한다.

로드맵 원문은 펼치기 전에 가져오지도 그리지도 않는다 — 10개 분야 × 13KB를 목록
응답에 실을 이유가 없고, 텍스트영역이 표를 밀어낸다.

종합보고서 칸을 항상 선택 가능하게 둔 이유: 세부기술 보고서 유무는 이 응답에 없어
화면이 미리 판단하려면 현황을 또 읽어야 한다. 서버가 skipped 사유로 알려 준다."
```

---

### Task 6: `Admin.tsx` — 탭 5개를 4개로

**Files:**
- Modify: `frontend/src/pages/Admin.tsx`
- Delete: `frontend/src/components/FieldReportsPanel.tsx`, `frontend/src/components/RoadmapEditor.tsx`
- Modify: `frontend/package.json` (version → `0.32.0`)

**Interfaces:**
- Consumes: Task 5의 `FieldTab`

- [ ] **Step 1: 탭 목록을 바꾼다**

`roadmap`과 `field-reports` 두 항목을 `field` 하나로 바꾼다.

```tsx
const TABS = [
  { id: "subfields", label: "검색식 관리" },
  { id: "subfield", label: "세부기술 분석" },
  { id: "field", label: "분야 보고서" },
  { id: "schedule", label: "자동 스케줄" },
] as const;
```

순서는 **작업 단위가 커지는 순**이다 — 검색식(설정) → 세부기술 → 분야 → 자동화.

- [ ] **Step 2: 렌더 블록을 교체한다**

`{tab === "roadmap" && ...}`과 `{tab === "field-reports" && ...}`를 지우고 하나로 바꾼다.

```tsx
        {tab === "field" && <FieldTab adminKey={key} onUnauthorized={onUnauthorized} />}
```

import에 `FieldTab`을 더하고 `FieldReportsPanel`·`RoadmapEditor`를 뺀다. **`fields` 상태와 `loadFields`가 다른 곳에서 쓰이는지 확인한다** — `RoadmapEditor`가 유일한 소비자였다면 함께 지운다(`npm run build`가 미사용 import를 잡아 준다).

- [ ] **Step 3: 죽은 컴포넌트를 지운다**

```bash
git rm frontend/src/components/FieldReportsPanel.tsx frontend/src/components/RoadmapEditor.tsx
```

- [ ] **Step 4: 버전을 올린다**

`frontend/package.json`의 `version`을 `0.31.3` → `0.32.0`으로.

- [ ] **Step 5: 게이트를 전부 돌린다**

```bash
cd frontend && ln -sfn /home/dev/code/performance-review/frontend/node_modules node_modules
npm run build && npm run lint && npm test
```
Expected: 전부 통과. 삭제가 많은 태스크라 미사용 import·죽은 상태가 남기 쉽다 — build가 잡는다.

- [ ] **Step 6: 커밋**

```bash
rm -f frontend/node_modules
git add -A
git commit -m "refactor: 관리자 탭 5개 → 4개, 분야 관련 2개를 하나로

분야 보고서와 전략기술로드맵이 같은 대상(분야)을 다루면서 두 탭에 나뉘어 있었다.
로드맵은 점검 보고서의 입력이므로 '점검이 안 된다'와 '로드맵이 없다'가 한 화면에서
이어져야 한다.

탭 순서는 작업 단위가 커지는 순이다 — 검색식(설정) → 세부기술 → 분야 → 자동화."
```

---

## 3단계 완료 조건

- 백엔드 **403 passed**(399 + 4) · 프론트 build ✓ · lint 0경고 · vitest **61/61**
- 관리자 탭이 **4개**로 줄고, 분야 탭에서 현황 확인·생성·로드맵 편집이 모두 된다
- 설계 문서(`2026-08-05-admin-ia-design.md`)가 목표로 적은 4탭 구성이 완성된다

## 브라우저로만 확인 가능한 것

jsdom이 없어 자동 검증할 수 없다. 배포 후 눈으로 볼 것:

1. 로드맵 미등록 분야의 점검 칸이 `대상 아님`이고 **체크박스가 없는지**
2. 열 머리글 체크가 부분 상태(`indeterminate`)로 보이는지
3. `[등록]`을 누르면 그 자리에서 편집기가 펼쳐지고, 저장 후 로드맵 열이 판본·목표 수로 바뀌는지
4. 표가 아닌 줄글을 저장하면 422 사유가 화면에 보이는지
5. 375px에서 표가 자기 컨테이너 안에서만 가로 스크롤되는지

## 이 계획에 없는 것

- **4단계**: 구 엔드포인트 제거(`/admin/run` · `field-reports/run-all` · `comparisons/run-all` · `comparison-grid`)와 `api.ts`의 죽은 export 정리, `field_reports_overview`의 `has_roadmap`(이제 `roadmap`이 대신한다) 제거.
- `lib/selection.ts`의 `ACTIVE_ANALYSIS_STATUSES`가 `api.ts`의 `ACTIVE_STATUSES`와 중복인 문제(2단계 이월).
- 분야 종합보고서의 **입력이 국가를 거르지 않는** 비대칭(`reducer.collect_subfield_reports`는 전 국가, 부록은 KR만) — 서비스 정의 문제라 코드 정리 범위 밖이다.
