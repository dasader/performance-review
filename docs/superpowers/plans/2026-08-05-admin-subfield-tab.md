# 관리자 세부기술 탭 (2단계) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `분석 실행·상태` · `국가 현황` · `국가 비교` 세 탭을 **세부기술** 탭 하나로 합친다. 기술 × 국가 현황을 한 표에 그리고, 셀을 체크해 `POST /admin/queue` 한 번으로 생성한다.

**Architecture:** 1단계가 만든 `GET /admin/dashboard`(비교 상태 포함)와 `POST /admin/queue`만 쓴다. 백엔드는 건드리지 않는다. 선택·비용 계산 같은 판단은 전부 `lib/`의 순수 함수로 빼서 vitest로 고정하고, 컴포넌트는 그 결과를 그리기만 한다 — 이 저장소에는 jsdom이 없어 렌더링을 자동 검증할 수 없기 때문이다.

**Tech Stack:** React 19 · TypeScript · Vite · Tailwind · vitest (순수 함수 전용)

## Global Constraints

- 프론트 게이트 3종을 **전부** 통과해야 한다:
  `cd frontend && npm run build` (tsc -b + vite build — 타입 오류는 여기서만 잡힌다)
  `cd frontend && npm run lint` (oxlint — 경고 0이 현재 상태다. 늘리지 말 것)
  `cd frontend && npm test` (vitest)
- **워크트리에는 `node_modules`가 없다.** 첫 명령 전에 한 번 링크한다:
  `cd frontend && ln -sfn /home/dev/code/performance-review/frontend/node_modules node_modules`
  커밋 전에는 반드시 지운다: `rm -f frontend/node_modules` (심링크가 커밋되면 안 된다)
- **레이아웃 간격은 6값뿐이다**(4/8/12/16/24/40 = Tailwind `1·2·3·4·6·10`). `src/lib/spacing.test.ts`가 `.tsx` 전체를 훑어 고정하므로 `py-2.5` 같은 값을 쓰면 `npm test`에서 바로 깨진다.
- **버튼·입력·표는 로컬 클래스로 다시 조립하지 말고 계약을 쓴다** — `.btn*` · `.input` · `.tbl-head` · `.table-scroll`(`src/index.css`의 `@layer components`). 넓은 표는 반드시 `.table-scroll`로 감싼다(`overflow-x-auto` 단독 금지 — 375px에서 문서 전체가 가로로 밀린다).
- `frontend/package.json`의 `version`을 올린다(기능 추가이므로 minor: `0.30.0` → `0.31.0`).
- 커밋 메시지는 한국어로 쓰고 **왜**를 남긴다.
- 401은 기존 규약을 그대로 쓴다: `e instanceof ApiError && e.status === 401` → `onUnauthorized()`.

## 이 단계에서 지우는 것 / 남기는 것

- **지운다**: `ComparisonGrid.tsx`, `ComparisonPanel.tsx`, `Admin.tsx`의 대시보드 표 블록, `TABS`의 `run`·`comparison`·`comparison-grid` 항목.
- **남긴다**: `RunDialog.tsx`(정밀 견적 전용으로 축소해 세부기술 탭 안에서 쓴다), 백엔드 엔드포인트 전부(4단계에서 정리).

## File Structure

| 파일 | 책임 |
|---|---|
| `frontend/src/lib/selection.ts` (신규) | 셀 키 인코딩, 선택 집합 연산, `/admin/queue` 요청 본문 조립. UI 없음 |
| `frontend/src/lib/selection.test.ts` (신규) | 위의 단위 테스트 |
| `frontend/src/lib/cost.ts` (신규) | 보고서 단가 상수 + 선택분 비용 추정 |
| `frontend/src/lib/cost.test.ts` (신규) | 위의 단위 테스트 |
| `frontend/src/api.ts` (수정) | `DashboardYearCell.country`, `DashboardRow.comparisons`, `QueueRequest`/`QueueResponse`, `queueAll()` |
| `frontend/src/components/SubfieldTab.tsx` (신규) | 표 + 선택 + 생성 바. `lib/`의 순수 함수를 그리기만 한다 |
| `frontend/src/components/RunDialog.tsx` (수정) | `locked` prop을 받아 정밀 견적 전용 고정 모드를 갖는다 |
| `frontend/src/pages/Admin.tsx` (수정) | 탭 3개 → 1개 |

---

### Task 1: `lib/selection.ts` — 셀 키와 선택 집합

**Files:**
- Create: `frontend/src/lib/selection.ts`
- Test: `frontend/src/lib/selection.test.ts`

**Interfaces:**
- Consumes: 없음 (순수 함수, 의존성 없음)
- Produces: `type Cell`, `cellKey(cell): string`, `parseCellKey(key): Cell`, `rowCells(subfieldId, countries, hasComparison): Cell[]`, `headerState(selected, candidates): "none"|"some"|"all"`, `toggleAll(selected, candidates, on): Set<string>`. Task 2·4·5가 전부 이것을 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/lib/selection.test.ts`를 새로 만든다.

```ts
import { describe, expect, it } from "vitest";
import { cellKey, parseCellKey, rowCells, headerState, toggleAll } from "./selection";

describe("셀 키", () => {
  it("분석 셀은 기술과 국가로 식별된다 — 같은 기술이라도 국가가 다르면 다른 대상이다", () => {
    expect(cellKey({ kind: "analysis", subfieldId: 3, country: "US" })).toBe("analysis:3:US");
    expect(cellKey({ kind: "analysis", subfieldId: 3, country: "KR" })).not.toBe(
      cellKey({ kind: "analysis", subfieldId: 3, country: "US" }),
    );
  });

  it("비교 셀은 기술 하나로 식별된다 — 국가 조합은 스케줄 설정이 정한다", () => {
    expect(cellKey({ kind: "comparison", subfieldId: 3 })).toBe("comparison:3");
  });

  it("왕복 변환이 원본과 같다", () => {
    const cells = [
      { kind: "analysis", subfieldId: 12, country: "CN" },
      { kind: "comparison", subfieldId: 12 },
    ] as const;
    for (const cell of cells) {
      expect(parseCellKey(cellKey(cell))).toEqual(cell);
    }
  });
});

describe("행의 셀 목록", () => {
  it("국가마다 분석 셀 하나 + 비교 셀 하나", () => {
    expect(rowCells(3, ["KR", "US"], true).map(cellKey)).toEqual([
      "analysis:3:KR",
      "analysis:3:US",
      "comparison:3",
    ]);
  });

  it("비교를 만들 수 없는 행은 비교 셀을 내지 않는다", () => {
    expect(rowCells(3, ["KR"], false).map(cellKey)).toEqual(["analysis:3:KR"]);
  });
});

describe("머리글 체크 상태", () => {
  const candidates = ["analysis:1:KR", "analysis:2:KR"];

  it("하나도 안 골랐으면 none", () => {
    expect(headerState(new Set(), candidates)).toBe("none");
  });

  it("일부만 골랐으면 some — 부분 상태를 보여줘야 전체 선택이 아님을 알 수 있다", () => {
    expect(headerState(new Set(["analysis:1:KR"]), candidates)).toBe("some");
  });

  it("전부 골랐으면 all", () => {
    expect(headerState(new Set(candidates), candidates)).toBe("all");
  });

  it("후보가 없으면 none — 선택할 것이 없는 열을 all로 보이면 안 된다", () => {
    expect(headerState(new Set(["analysis:1:KR"]), [])).toBe("none");
  });
});

describe("일괄 토글", () => {
  it("켜면 후보를 더하되 기존 선택은 건드리지 않는다", () => {
    const next = toggleAll(new Set(["comparison:9"]), ["analysis:1:KR"], true);
    expect([...next].sort()).toEqual(["analysis:1:KR", "comparison:9"]);
  });

  it("끄면 후보만 빼고 나머지는 남긴다", () => {
    const next = toggleAll(new Set(["analysis:1:KR", "comparison:9"]), ["analysis:1:KR"], false);
    expect([...next]).toEqual(["comparison:9"]);
  });
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/lib/selection.test.ts`
Expected: FAIL — `Failed to resolve import "./selection"`.

- [ ] **Step 3: 구현한다**

`frontend/src/lib/selection.ts`를 새로 만든다.

```ts
// 관리자 세부기술 탭의 선택 상태. 컴포넌트가 아니라 여기서 판단한다 —
// 이 저장소에는 jsdom이 없어 렌더링을 자동 검증할 수 없으므로, 검증할 수 있는
// 형태(순수 함수)로 최대한 빼낸다.

export type CellKind = "analysis" | "comparison";

// 화면의 셀 하나 = 만들 수 있는 산출물 하나.
// 분석은 기술 × 국가, 비교는 기술 하나(국가 조합은 스케줄 설정이 정한다).
// 연도는 화면 전역 필터라 키에 넣지 않는다 — 연도를 바꾸면 선택을 비운다.
export interface Cell {
  kind: CellKind;
  subfieldId: number;
  country?: string;
}

export function cellKey(cell: Cell): string {
  return cell.kind === "analysis"
    ? `analysis:${cell.subfieldId}:${cell.country}`
    : `comparison:${cell.subfieldId}`;
}

export function parseCellKey(key: string): Cell {
  const [kind, id, country] = key.split(":");
  return kind === "analysis"
    ? { kind: "analysis", subfieldId: Number(id), country }
    : { kind: "comparison", subfieldId: Number(id) };
}

export function rowCells(
  subfieldId: number,
  countries: string[],
  hasComparison: boolean,
): Cell[] {
  const cells: Cell[] = countries.map((country) => ({
    kind: "analysis",
    subfieldId,
    country,
  }));
  if (hasComparison) cells.push({ kind: "comparison", subfieldId });
  return cells;
}

// 부분 상태를 구분하는 이유: 열 머리글이 all/none만 보이면 "일부만 골랐는데
// 전체가 선택된 것처럼" 읽힌다.
export function headerState(
  selected: Set<string>,
  candidates: string[],
): "none" | "some" | "all" {
  if (candidates.length === 0) return "none";
  const hits = candidates.filter((key) => selected.has(key)).length;
  if (hits === 0) return "none";
  return hits === candidates.length ? "all" : "some";
}

export function toggleAll(
  selected: Set<string>,
  candidates: string[],
  on: boolean,
): Set<string> {
  const next = new Set(selected);
  for (const key of candidates) {
    if (on) next.add(key);
    else next.delete(key);
  }
  return next;
}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run src/lib/selection.test.ts`
Expected: PASS — 9건.

- [ ] **Step 5: 커밋**

```bash
rm -f frontend/node_modules
git add frontend/src/lib/selection.ts frontend/src/lib/selection.test.ts
git commit -m "feat: 관리자 셀 선택 로직을 순수 함수로 분리

세부기술 탭의 핵심은 '체크한 것을 요청 본문으로 바꾸는' 판단인데, 이 저장소에는
jsdom이 없어 컴포넌트 안에 두면 자동 검증할 수단이 없다. 검증 가능한 형태로 뺀다.

연도를 셀 키에 넣지 않는 이유: 연도는 화면 전역 필터이고 바뀌면 선택을 비운다 —
키에 넣으면 다른 연도 대상이 선택에 섞인 채로 남는다."
```

---

### Task 2: `lib/selection.ts` — 요청 본문 조립

**Files:**
- Modify: `frontend/src/lib/selection.ts`
- Test: `frontend/src/lib/selection.test.ts`

**Interfaces:**
- Consumes: Task 1의 `Cell`, `cellKey`, `parseCellKey`
- Produces: `toQueuePayload(selected: Set<string>, opts: {year: number; countries: string[]; force: boolean}): QueueRequestBody`, 그리고 그 타입 `QueueRequestBody`(`{year, analyses, comparisons, field_reports, roadmap_checks}`). Task 3의 `queueAll()`과 Task 5의 생성 바가 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/lib/selection.test.ts` 맨 끝에 추가한다. import 줄에 `toQueuePayload`를 더한다.

```ts
describe("요청 본문 조립", () => {
  const opts = { year: 2026, countries: ["KR", "US", "CN"], force: false };

  it("분석은 기술·국가별 항목으로, 비교는 설정된 국가 전체로 나간다", () => {
    const selected = new Set(["analysis:3:US", "analysis:7:KR", "comparison:3"]);
    expect(toQueuePayload(selected, opts)).toEqual({
      year: 2026,
      analyses: [
        { subfield_id: 3, country: "US", force: false },
        { subfield_id: 7, country: "KR", force: false },
      ],
      comparisons: [{ subfield_id: 3, countries: ["KR", "US", "CN"] }],
      field_reports: [],
      roadmap_checks: [],
    });
  });

  it("아무것도 안 골랐으면 빈 목록만 담는다 — 연도는 그대로 실린다", () => {
    expect(toQueuePayload(new Set(), opts)).toEqual({
      year: 2026,
      analyses: [],
      comparisons: [],
      field_reports: [],
      roadmap_checks: [],
    });
  });

  it("force는 모든 분석 항목에 실린다 — 완료된 것을 다시 만드는 유일한 수단이다", () => {
    const payload = toQueuePayload(new Set(["analysis:3:KR"]), { ...opts, force: true });
    expect(payload.analyses).toEqual([{ subfield_id: 3, country: "KR", force: true }]);
  });

  it("항목 순서가 안정적이다 — 확인 문구와 결과 대조가 실행마다 흔들리면 안 된다", () => {
    const a = toQueuePayload(new Set(["analysis:7:KR", "analysis:3:US"]), opts);
    const b = toQueuePayload(new Set(["analysis:3:US", "analysis:7:KR"]), opts);
    expect(a).toEqual(b);
  });
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/lib/selection.test.ts`
Expected: FAIL — `toQueuePayload is not a function`.

- [ ] **Step 3: 구현한다**

`frontend/src/lib/selection.ts` 맨 끝에 추가한다.

```ts
// POST /admin/queue의 요청 본문. 백엔드 QueueIn과 같은 모양이어야 한다.
export interface QueueRequestBody {
  year: number;
  analyses: { subfield_id: number; country: string; force: boolean }[];
  comparisons: { subfield_id: number; countries: string[] }[];
  field_reports: number[];
  roadmap_checks: number[];
}

// 체크한 셀들을 요청 하나로 바꾼다. 세부기술 탭은 분야 산출물을 다루지 않으므로
// field_reports·roadmap_checks는 항상 빈 배열이다(분야 탭이 3단계에서 채운다).
//
// 키를 정렬해 순회하는 이유: Set의 순회 순서는 삽입 순서라 같은 선택도 클릭 순서에
// 따라 다른 본문이 된다. 확인 문구와 결과 목록을 대조할 때 흔들리면 읽기 어렵다.
export function toQueuePayload(
  selected: Set<string>,
  opts: { year: number; countries: string[]; force: boolean },
): QueueRequestBody {
  const body: QueueRequestBody = {
    year: opts.year,
    analyses: [],
    comparisons: [],
    field_reports: [],
    roadmap_checks: [],
  };
  for (const key of [...selected].sort()) {
    const cell = parseCellKey(key);
    if (cell.kind === "analysis") {
      body.analyses.push({
        subfield_id: cell.subfieldId,
        country: cell.country as string,
        force: opts.force,
      });
    } else {
      body.comparisons.push({ subfield_id: cell.subfieldId, countries: opts.countries });
    }
  }
  return body;
}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run src/lib/selection.test.ts`
Expected: PASS — 13건.

- [ ] **Step 5: 커밋**

```bash
rm -f frontend/node_modules
git add frontend/src/lib/selection.ts frontend/src/lib/selection.test.ts
git commit -m "feat: 선택 집합 → /admin/queue 요청 본문 변환

키를 정렬해 순회한다 — Set은 삽입 순서라 같은 선택도 클릭 순서에 따라 다른 본문이
되고, 확인 문구와 결과 목록을 대조할 때 흔들린다."
```

---

### Task 3: `lib/cost.ts` — 선택분 비용 추정

**Files:**
- Create: `frontend/src/lib/cost.ts`
- Test: `frontend/src/lib/cost.test.ts`

**Interfaces:**
- Consumes: Task 2의 `QueueRequestBody`
- Produces: `estimateCost(body: QueueRequestBody, papersByCell: Record<string, number>): {reportUsd: number; analysisCount: number; analysisPapers: number | null}`. Task 5의 생성 바가 쓴다.

**왜 이런 모양인가:** 분석 비용은 미리 알 수 없다 — `/admin/preview`가 OpenAlex를 실제로 호출해야 건수를 알 수 있고 그 호출 자체가 과금이라, 셀을 고를 때마다 돈이 나가는 화면은 만들 수 없다. 그래서 **보고서류만 금액**으로 내고, 분석은 건수와 (과거 실적이 있으면) 참고 논문 수만 낸다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/lib/cost.test.ts`를 새로 만든다.

```ts
import { describe, expect, it } from "vitest";
import { COMPARISON_USD, estimateCost } from "./cost";
import type { QueueRequestBody } from "./selection";

const empty: QueueRequestBody = {
  year: 2026, analyses: [], comparisons: [], field_reports: [], roadmap_checks: [],
};

describe("선택분 비용 추정", () => {
  it("비교는 실측 단가로 금액이 나온다", () => {
    const body = { ...empty, comparisons: [{ subfield_id: 1, countries: ["KR", "US"] }] };
    expect(estimateCost(body, {})).toEqual({
      reportUsd: COMPARISON_USD,
      analysisCount: 0,
      analysisPapers: null,
    });
  });

  it("분석은 금액을 내지 않는다 — 검색 전에는 건수를 알 수 없다", () => {
    const body = { ...empty, analyses: [{ subfield_id: 1, country: "KR", force: false }] };
    expect(estimateCost(body, {}).reportUsd).toBe(0);
    expect(estimateCost(body, {}).analysisCount).toBe(1);
  });

  it("과거 실적이 있으면 참고 논문 수를 합산한다", () => {
    const body = {
      ...empty,
      analyses: [
        { subfield_id: 1, country: "KR", force: false },
        { subfield_id: 1, country: "US", force: false },
      ],
    };
    const papers = { "analysis:1:KR": 278, "analysis:1:US": 445 };
    expect(estimateCost(body, papers).analysisPapers).toBe(723);
  });

  it("실적이 하나도 없으면 참고값을 내지 않는다 — 0편으로 보이면 안 된다", () => {
    const body = { ...empty, analyses: [{ subfield_id: 9, country: "JP", force: false }] };
    expect(estimateCost(body, {}).analysisPapers).toBeNull();
  });

  it("일부만 실적이 있으면 있는 것만 더한다", () => {
    const body = {
      ...empty,
      analyses: [
        { subfield_id: 1, country: "KR", force: false },
        { subfield_id: 9, country: "JP", force: false },
      ],
    };
    expect(estimateCost(body, { "analysis:1:KR": 278 }).analysisPapers).toBe(278);
  });
});
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/lib/cost.test.ts`
Expected: FAIL — `Failed to resolve import "./cost"`.

- [ ] **Step 3: 구현한다**

`frontend/src/lib/cost.ts`를 새로 만든다.

```ts
import { cellKey, type QueueRequestBody } from "./selection";

// 실측 단가(2026-08-05, 데이터·AI 보안 2025 KR 489건 · 바이오 데이터·AI 2026 KR 501건).
// gemini-3.1-flash-lite / thinking=high 기준이다.
//
// **모델이나 THINKING_REDUCE를 바꾸면 이 값도 손봐야 한다** — 같은 측정에서
// gemini-3.6-flash는 단일 reduce가 7배, 3단 reduce가 8배였다.
// 설정으로 빼지 않는 이유: 어차피 추정치이고, 설정으로 만들면 관리 지점만 는다.
export const COMPARISON_USD = 0.05;
export const FIELD_REPORT_USD = 0.03;

export interface CostEstimate {
  /** 보고서류 예상 금액(USD). 분석은 포함하지 않는다. */
  reportUsd: number;
  /** 선택된 분석 건수. */
  analysisCount: number;
  /** 참고 논문 수 합계. 과거 실적이 하나도 없으면 null. */
  analysisPapers: number | null;
}

// 분석 비용을 금액으로 내지 않는 이유: /admin/preview가 OpenAlex를 실제로 호출해야
// 건수를 알 수 있고 그 호출 자체가 과금이다. 셀을 고를 때마다 돈이 나가는 화면은
// 만들 수 없다. 대신 같은 셀의 과거 검색 건수를 참고값으로 보여준다.
export function estimateCost(
  body: QueueRequestBody,
  papersByCell: Record<string, number>,
): CostEstimate {
  const reportUsd =
    body.comparisons.length * COMPARISON_USD +
    (body.field_reports.length + body.roadmap_checks.length) * FIELD_REPORT_USD;

  let papers: number | null = null;
  for (const item of body.analyses) {
    const known = papersByCell[
      cellKey({ kind: "analysis", subfieldId: item.subfield_id, country: item.country })
    ];
    if (known !== undefined) papers = (papers ?? 0) + known;
  }

  return { reportUsd, analysisCount: body.analyses.length, analysisPapers: papers };
}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd frontend && npx vitest run src/lib/cost.test.ts`
Expected: PASS — 5건.

- [ ] **Step 5: 커밋**

```bash
rm -f frontend/node_modules
git add frontend/src/lib/cost.ts frontend/src/lib/cost.test.ts
git commit -m "feat: 선택분 비용 추정 — 보고서는 금액, 분석은 건수만

분석 비용은 미리 알 수 없다. /admin/preview가 OpenAlex를 실제로 호출해야 건수를
알 수 있고 그 호출 자체가 과금이라, 셀을 고를 때마다 돈이 나가는 화면이 된다.
대신 같은 셀의 과거 검색 건수를 참고값으로 낸다 — 실적이 없으면 null이다
(0편으로 보이면 '없다'와 '모른다'가 구별되지 않는다)."
```

---

### Task 4: `api.ts` — 타입과 `queueAll()`

**Files:**
- Modify: `frontend/src/api.ts` (`DashboardYearCell` 307-317행, `DashboardRow` 319-324행 부근)

**Interfaces:**
- Consumes: Task 2의 `QueueRequestBody`
- Produces: `DashboardYearCell.country: string`, `DashboardRow.comparisons: Record<string, Record<string, string>>`, `interface QueueResponse`, `queueAll(body, adminKey): Promise<QueueResponse>`. Task 5가 전부 쓴다.

- [ ] **Step 1: 타입을 실제 응답에 맞춘다**

`DashboardYearCell`에 `country`를 더한다. **백엔드는 이 값을 오래전부터 보내고 있었는데 타입에 없었다** — 그래서 현재 표가 국가를 구분하지 않고 같은 기술을 중복 행처럼 그린다.

```ts
export interface DashboardYearCell {
  analysis_id: number;
  year: number;
  status: string;
  status_label: string;
  searched_count: number;
  analyzed_count: number;
  snapshot_at: string | null;
  stale: boolean;
  error: string | null;
  // 같은 세부기술·연도라도 국가가 다르면 다른 분석이다(analyses의 유일키에 country가 있다).
  country: string;
}
```

`DashboardRow`에 1단계가 추가한 비교 상태를 더한다.

```ts
export interface DashboardRow {
  subfield_id: number;
  subfield_name: string;
  field_id: number;
  years: DashboardYearCell[];
  // 연도(문자열) → 정렬된 콤마 국가키 → 상태. 상태가 "in_multi"면 그 1:1이 다국
  // 비교 안에 이미 들어 있다는 뜻이다(따로 만들 필요가 없다).
  comparisons: Record<string, Record<string, string>>;
}
```

- [ ] **Step 2: 응답 타입과 호출 함수를 추가한다**

`DashboardResponse` 아래에 넣는다.

```ts
// POST /admin/queue의 응답. skipped는 조용히 건너뛰지 않기 위한 것이라
// 화면이 사유를 그대로 보여준다 — 문자열을 매칭해 분기하지 말 것.
export interface QueueResponse {
  queued: {
    analyses: number;
    comparisons: number;
    field_reports: number;
    roadmap_checks: number;
  };
  skipped: {
    kind: "analysis" | "comparison" | "field_report" | "roadmap_check";
    subfield_id?: number;
    field_id?: number;
    country?: string;
    countries?: string[];
    reason: string;
  }[];
}

export function queueAll(body: QueueRequestBody, adminKey: string) {
  return post<QueueResponse>("/admin/queue", body, adminKey);
}
```

`api.ts` 맨 위 import에 타입을 더한다.

```ts
import type { QueueRequestBody } from "./lib/selection";
```

- [ ] **Step 3: 타입 검사를 통과시킨다**

Run: `cd frontend && ln -sfn /home/dev/code/performance-review/frontend/node_modules node_modules && npm run build`
Expected: 성공.

`comparisons`·`country`를 **선택적(`?`)으로 두지 말 것.** 기존 코드는 `DashboardRow`를 읽기만 하고 새로 만들지 않으므로(`Admin.tsx`는 API 응답을 그대로 받아 쓴다) 필수 필드를 더해도 깨지지 않는다. 선택적으로 두면 Task 5가 `row.comparisons`를 쓸 때마다 옵셔널 체이닝이 붙어, 백엔드가 항상 보내는 값이 "없을 수도 있는 값"으로 위장된다.

- [ ] **Step 4: 커밋**

```bash
rm -f frontend/node_modules
git add frontend/src/api.ts
git commit -m "feat: dashboard 타입에 country·comparisons, /admin/queue 호출 추가

DashboardYearCell.country는 백엔드가 오래전부터 보내던 값인데 타입에 없었다 —
그래서 현재 표가 국가를 구분하지 못하고 같은 기술을 중복 행처럼 그린다."
```

---

### Task 5: `SubfieldTab.tsx` — 표와 선택, 생성

**Files:**
- Create: `frontend/src/components/SubfieldTab.tsx`

**Interfaces:**
- Consumes: Task 1-2의 `selection.ts` 전부, Task 3의 `estimateCost`·`COMPARISON_USD`, Task 4의 `DashboardResponse`·`queueAll`·`QueueResponse`, 그리고 기존 `StatusBadge`, `YearInput`, `usePolling`, `ACTIVE_STATUSES`, `STATUS_LABEL`, `COUNTRY_NAMES`, `sortCountries`, `ApiError`, `get`
- Produces: `export default function SubfieldTab({adminKey, onUnauthorized}: {adminKey: string; onUnauthorized: () => void})`. Task 6이 `Admin.tsx`에서 쓴다.

**설계 결정 (스펙이 정하지 않은 것을 여기서 못박는다):**
- 국가 열은 **스케줄 설정의 국가 + 실제로 분석이 존재하는 국가의 합집합**으로 만든다. 설정에 없는 국가의 기존 분석이 화면에서 사라지면 안 된다(현행 `ComparisonGrid`가 이미 이 규칙을 쓴다).
- 비교 열은 **다국 비교 하나**만 그린다. 설정 국가가 2개 미만이면 열 자체를 내지 않는다.
- 재실행·삭제 같은 **개별 분석 동작은 행 펼침 안**에 둔다. 표를 좁게 유지하고, 셀은 "선택해서 만드는 것"이라는 한 가지 뜻만 갖게 한다.
- 연도를 바꾸면 **선택을 비운다**. 다른 연도 대상이 선택에 남으면 잘못 큐잉된다.
- 폴링 갱신(5초)은 선택을 건드리지 않는다. 갱신 후 사라진 대상은 선택에서 조용히 뺀다.

- [ ] **Step 1: 컴포넌트를 만든다**

`frontend/src/components/SubfieldTab.tsx`를 새로 만든다.

```tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ACTIVE_STATUSES,
  ApiError,
  STATUS_LABEL,
  get,
  queueAll,
  type DashboardResponse,
  type DashboardRow,
  type QueueResponse,
} from "../api";
import { COUNTRY_NAMES, sortCountries } from "../lib/countries";
import { usePolling } from "../lib/hooks";
import { estimateCost } from "../lib/cost";
import { cellKey, headerState, rowCells, toQueuePayload, toggleAll } from "../lib/selection";
import StatusBadge from "./StatusBadge";
import YearInput from "./YearInput";

// 관리자 "세부기술" 탭 — 기술 × 국가 현황과 생성이 한 화면에 있다.
//
// 예전에는 "분석 실행·상태"(연도 축, 국가 미표시) · "국가 현황"(국가 축, 일괄 생성만) ·
// "국가 비교"(현황 없이 임의 조합 생성) 세 탭에 흩어져 있었다. 같은 대상을 보는 곳과
// 만드는 곳이 달라, 무엇이 어디까지 됐는지도 이 버튼이 무엇을 대상으로 하는지도
// 알기 어려웠다. 셀을 체크해 고르면 대상이 눈으로 보인다.
export default function SubfieldTab({
  adminKey,
  onUnauthorized,
}: {
  adminKey: string;
  onUnauthorized: () => void;
}) {
  const [year, setYear] = useState(new Date().getFullYear());
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [scheduleCountries, setScheduleCountries] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QueueResponse | null>(null);

  const load = useCallback(() => {
    get<DashboardResponse>("/admin/dashboard", adminKey)
      .then(setData)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) return onUnauthorized();
        setError(e instanceof Error ? e.message : "현황을 불러오지 못했습니다.");
      });
    get<{ countries: string }>("/admin/schedule", adminKey)
      .then((s) => setScheduleCountries(s.countries.split(",").filter(Boolean)))
      .catch(() => setScheduleCountries([]));
  }, [adminKey, onUnauthorized]);

  useEffect(load, [load]);

  // 연도를 바꾸면 선택을 비운다 — 다른 연도 대상이 선택에 남으면 잘못 큐잉된다.
  useEffect(() => setSelected(new Set()), [year]);

  const rows = data?.rows ?? [];

  // 열은 설정된 국가 + 실제로 분석이 있는 국가의 합집합이다. 설정에 없는 국가의
  // 기존 분석이 화면에서 사라지면 "안 돌렸다"와 구별되지 않는다.
  const countries = useMemo(() => {
    const present = new Set(scheduleCountries);
    for (const row of rows) {
      for (const cell of row.years) if (cell.year === year) present.add(cell.country);
    }
    return sortCountries([...present]);
  }, [rows, scheduleCountries, year]);

  // 비교는 설정된 국가 전체를 한 보고서로 만든다. 2개 미만이면 만들 수 없다.
  const comparisonKey = useMemo(
    () => [...scheduleCountries].sort().join(","),
    [scheduleCountries],
  );
  const showComparison = scheduleCountries.length >= 2;

  const cellOf = (row: DashboardRow, country: string) =>
    row.years.find((c) => c.year === year && c.country === country);

  // 상대국 분석이 하나라도 없으면 비교를 만들 수 없다 — 선택 자체를 막는다.
  const comparisonBlocked = (row: DashboardRow) =>
    !scheduleCountries.every((c) => cellOf(row, c)?.status === "done");

  const selectableOf = (row: DashboardRow) =>
    rowCells(row.subfield_id, countries, showComparison && !comparisonBlocked(row));

  const allCandidates = useMemo(
    () => rows.flatMap((row) => selectableOf(row).map(cellKey)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, countries, showComparison, scheduleCountries, year],
  );

  // 진행 중인 것이 있으면 5초마다 다시 읽는다. 선택은 건드리지 않는다.
  const hasActive = rows.some((row) =>
    row.years.some((c) => c.year === year && ACTIVE_STATUSES.has(c.status)),
  );
  usePolling(hasActive, load);

  // 갱신으로 사라진 대상은 선택에서 조용히 뺀다.
  useEffect(() => {
    setSelected((prev) => {
      const valid = new Set(allCandidates);
      const next = new Set([...prev].filter((k) => valid.has(k)));
      return next.size === prev.size ? prev : next;
    });
  }, [allCandidates]);

  const papersByCell = useMemo(() => {
    const map: Record<string, number> = {};
    for (const row of rows) {
      for (const c of row.years) {
        if (c.year === year && c.searched_count > 0) {
          map[cellKey({ kind: "analysis", subfieldId: row.subfield_id, country: c.country })] =
            c.searched_count;
        }
      }
    }
    return map;
  }, [rows, year]);

  const payload = toQueuePayload(selected, { year, countries: scheduleCountries, force });
  const cost = estimateCost(payload, papersByCell);
  const total = payload.analyses.length + payload.comparisons.length;

  const toggle = (key: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const runQueue = async () => {
    if (total === 0) return;
    if (!confirm(`${year}년 ${total}건을 생성합니다. LLM 호출 비용이 발생합니다. 계속할까요?`)) return;
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

  return (
    <section className="mt-6 border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-accent">세부기술 현황</h2>
        <YearInput year={year} onChange={setYear} />
      </div>

      <p className="mt-2 text-xs text-muted">
        셀 하나가 만들 수 있는 산출물 하나입니다. 체크해서 고르고 위에서 한 번에 생성합니다.
        열 머리글은 그 국가 전체, 행 체크는 그 기술 전체를 고릅니다.
        <strong className="text-ink"> —</strong>는 상대국 분석이 없어 지금은 만들 수 없는 칸입니다.
      </p>

      {/* 선택 요약 + 실행. 대상 건수를 눈으로 확인한 뒤 누르게 한다. */}
      <div className="mt-4 flex flex-wrap items-center gap-3 border border-border-light bg-paper p-3">
        <span className="text-sm text-ink">
          분석 {payload.analyses.length}건 · 비교 {payload.comparisons.length}건 선택됨
        </span>
        {cost.reportUsd > 0 && (
          <span className="text-xs text-muted">보고서 예상 ${cost.reportUsd.toFixed(2)}</span>
        )}
        {payload.analyses.length > 0 && (
          <span className="text-xs text-muted">
            {cost.analysisPapers === null
              ? "분석 비용은 검색 결과에 따라 달라집니다"
              : `분석은 과거 실적 ${cost.analysisPapers.toLocaleString()}편 기준`}
          </span>
        )}
        <label className="flex items-center gap-2 text-sm text-ink-light">
          <input type="checkbox" checked={force} onChange={() => setForce((v) => !v)} />
          이미 완료된 것도 다시 생성
        </label>
        <button
          type="button"
          onClick={runQueue}
          disabled={busy || total === 0}
          className="btn btn-primary btn-sm"
        >
          {busy ? "요청 중…" : `선택한 ${total}건 생성`}
        </button>
        {selected.size > 0 && (
          <button
            type="button"
            onClick={() => setSelected(new Set())}
            className="btn btn-neutral btn-sm"
          >
            선택 해제
          </button>
        )}
      </div>

      {error && <p className="mt-3 text-sm text-danger">{error}</p>}

      {/* 부분 실패를 사유와 함께 보여준다 — 조용히 건너뛰지 않는 것이 이 API의 요점이다. */}
      {result && (
        <div className="mt-3 border border-border-light bg-paper p-3 text-sm">
          <p className="text-ink">
            분석 {result.queued.analyses}건 · 비교 {result.queued.comparisons}건 큐잉됨
          </p>
          {result.skipped.length > 0 && (
            <ul className="mt-2 space-y-1">
              {result.skipped.map((s, i) => (
                <li key={i} className="text-xs text-muted">
                  {rows.find((r) => r.subfield_id === s.subfield_id)?.subfield_name ?? s.subfield_id}
                  {s.country ? ` · ${COUNTRY_NAMES[s.country] ?? s.country}` : ""} — {s.reason}
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
                <th>세부기술</th>
                {countries.map((c) => {
                  const candidates = rows
                    .map((row) => cellKey({ kind: "analysis", subfieldId: row.subfield_id, country: c }))
                    .filter((k) => allCandidates.includes(k));
                  const state = headerState(selected, candidates);
                  return (
                    <th key={c}>
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
                        {COUNTRY_NAMES[c] ?? c}
                      </label>
                    </th>
                  );
                })}
                {showComparison && <th>국가비교</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const candidates = selectableOf(row).map(cellKey);
                const rowState = headerState(selected, candidates);
                const open = expanded.has(row.subfield_id);
                return (
                  <ExpandableRow
                    key={row.subfield_id}
                    row={row}
                    year={year}
                    countries={countries}
                    open={open}
                    rowState={rowState}
                    selected={selected}
                    showComparison={showComparison}
                    comparisonStatus={row.comparisons[String(year)]?.[comparisonKey]}
                    comparisonBlocked={comparisonBlocked(row)}
                    onToggleRow={() =>
                      setSelected((prev) => toggleAll(prev, candidates, rowState !== "all"))
                    }
                    onToggleCell={toggle}
                    onToggleExpand={() =>
                      setExpanded((prev) => {
                        const next = new Set(prev);
                        if (next.has(row.subfield_id)) next.delete(row.subfield_id);
                        else next.add(row.subfield_id);
                        return next;
                      })
                    }
                    cellOf={cellOf}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// 행 하나 + 펼쳤을 때의 연도 이력. 개별 분석 동작(재실행·삭제)은 여기 둔다 —
// 표의 셀은 "선택해서 만드는 것"이라는 한 가지 뜻만 갖게 한다.
function ExpandableRow({
  row, year, countries, open, rowState, selected, showComparison,
  comparisonStatus, comparisonBlocked, onToggleRow, onToggleCell, onToggleExpand, cellOf,
}: {
  row: DashboardRow;
  year: number;
  countries: string[];
  open: boolean;
  rowState: "none" | "some" | "all";
  selected: Set<string>;
  showComparison: boolean;
  comparisonStatus: string | undefined;
  comparisonBlocked: boolean;
  onToggleRow: () => void;
  onToggleCell: (key: string) => void;
  onToggleExpand: () => void;
  cellOf: (row: DashboardRow, country: string) => { status: string; status_label: string; stale: boolean } | undefined;
}) {
  const history = row.years.filter((c) => c.year !== year).sort((a, b) => b.year - a.year);
  return (
    <>
      <tr className="border-b border-border-light">
        <td className="py-3 pr-3">
          <label className="flex items-center gap-2 font-medium text-ink">
            <input
              type="checkbox"
              checked={rowState === "all"}
              ref={(el) => {
                if (el) el.indeterminate = rowState === "some";
              }}
              onChange={onToggleRow}
            />
            <button type="button" onClick={onToggleExpand} className="text-left">
              <span aria-hidden="true">{open ? "▾" : "▸"}</span> {row.subfield_name}
            </button>
          </label>
        </td>
        {countries.map((c) => {
          const cell = cellOf(row, c);
          const key = cellKey({ kind: "analysis", subfieldId: row.subfield_id, country: c });
          return (
            <td key={c} className="py-3 pr-3 text-center">
              <label className="inline-flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={selected.has(key)}
                  onChange={() => onToggleCell(key)}
                />
                {cell ? (
                  <StatusBadge status={cell.status} label={STATUS_LABEL[cell.status] ?? cell.status} />
                ) : (
                  <span className="text-xs text-muted">미생성</span>
                )}
              </label>
              {cell?.stale && <p className="text-xs text-warning">갱신 필요</p>}
            </td>
          );
        })}
        {showComparison && (
          <td className="py-3 text-center">
            {comparisonBlocked && !comparisonStatus ? (
              <span className="text-faint" title="상대국 분석이 없어 지금은 만들 수 없습니다">
                —
              </span>
            ) : (
              <label className="inline-flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={selected.has(cellKey({ kind: "comparison", subfieldId: row.subfield_id }))}
                  disabled={comparisonBlocked}
                  onChange={() =>
                    onToggleCell(cellKey({ kind: "comparison", subfieldId: row.subfield_id }))
                  }
                />
                {comparisonStatus ? (
                  <StatusBadge
                    status={comparisonStatus}
                    label={STATUS_LABEL[comparisonStatus] ?? comparisonStatus}
                  />
                ) : (
                  <span className="text-xs text-muted">미생성</span>
                )}
              </label>
            )}
          </td>
        )}
      </tr>
      {open && (
        <tr className="border-b border-border-light bg-sunken">
          <td colSpan={countries.length + (showComparison ? 2 : 1)} className="py-3 pr-3">
            {history.length === 0 ? (
              <p className="text-xs text-muted">다른 연도의 분석이 없습니다.</p>
            ) : (
              <ul className="space-y-1">
                {history.map((c) => (
                  <li key={c.analysis_id} className="text-xs text-muted">
                    {c.year} · {COUNTRY_NAMES[c.country] ?? c.country} ·{" "}
                    {STATUS_LABEL[c.status] ?? c.status} · 검색 {c.searched_count.toLocaleString()} /
                    분석 {c.analyzed_count.toLocaleString()}
                    {c.stale && <span className="ml-2 text-warning">갱신 필요</span>}
                  </li>
                ))}
              </ul>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
```

- [ ] **Step 2: 게이트를 전부 돌린다**

```bash
cd frontend && ln -sfn /home/dev/code/performance-review/frontend/node_modules node_modules
npm run build && npm run lint && npm test
```
Expected: build 성공 · lint 경고 0 · 테스트 전부 통과(간격 검사 `spacing.test.ts` 포함). 간격 검사가 깨지면 6값(4/8/12/16/24/40) 밖의 Tailwind 간격을 쓴 것이니 그 클래스를 고친다.

- [ ] **Step 3: 커밋**

```bash
rm -f frontend/node_modules
git add frontend/src/components/SubfieldTab.tsx
git commit -m "feat: 세부기술 탭 — 기술 × 국가 현황과 생성을 한 화면에

예전에는 같은 대상을 보는 곳과 만드는 곳이 달랐다. '국가 비교'는 만들 수는 있는데
뭐가 있는지 안 보이고, '국가 현황'은 보이는데 전체 일괄밖에 못 만들었다.
셀을 체크해 고르면 대상이 눈으로 보이고, 이름 없는 '전체 생성'이 사라진다.

개별 분석 동작(재실행·삭제)은 행 펼침 안에 둔다 — 표의 셀은 '선택해서 만드는 것'이라는
한 가지 뜻만 갖게 한다."
```

---

### Task 6: `Admin.tsx` — 탭 3개를 1개로

**Files:**
- Modify: `frontend/src/pages/Admin.tsx` (`TABS` 28-36행, 탭 렌더 블록 231-367행)
- Delete: `frontend/src/components/ComparisonGrid.tsx`, `frontend/src/components/ComparisonPanel.tsx`
- Modify: `frontend/package.json` (version)

**Interfaces:**
- Consumes: Task 5의 `SubfieldTab`
- Produces: 탭 5개(`subfields` · `subfield` · `schedule` · `roadmap` · `field-reports`)로 줄어든 관리자 화면. 3단계가 `roadmap`+`field-reports`를 합쳐 4개로 만든다.

- [ ] **Step 1: 탭 목록을 바꾼다**

`TABS`에서 `run` · `comparison` · `comparison-grid`를 빼고 `subfield`를 넣는다.

```tsx
const TABS = [
  { id: "subfields", label: "세부기술·검색식" },
  { id: "subfield", label: "세부기술" },
  { id: "schedule", label: "자동 스케줄" },
  { id: "roadmap", label: "전략기술로드맵" },
  { id: "field-reports", label: "분야 보고서" },
] as const;
```

- [ ] **Step 2: 렌더 블록을 교체한다**

`{tab === "run" && …}` 블록 두 개(RunDialog 포함 블록과 대시보드 표 블록), `{tab === "comparison" && …}`, `{tab === "comparison-grid" && …}`를 전부 지우고 하나로 바꾼다.

```tsx
        {tab === "subfield" && (
          <SubfieldTab adminKey={key} onUnauthorized={onUnauthorized} />
        )}
```

import에 `SubfieldTab`을 더하고, 이제 쓰지 않는 것들을 지운다: `ComparisonGrid`, `ComparisonPanel`, `RunDialog`, `StatusBadge`, `ACTIVE_STATUSES`, `DashboardRow`, `post`, `del`, 그리고 `retryingId`·`deletingId`·`handleDeleteAnalysis` 상태와 함수. **`data`(대시보드 응답)와 `loadDashboard`는 남는지 확인한다** — 다른 탭이 쓰지 않으면 함께 지운다.

- [ ] **Step 3: 죽은 컴포넌트를 지운다**

```bash
git rm frontend/src/components/ComparisonGrid.tsx frontend/src/components/ComparisonPanel.tsx
```

`RunDialog.tsx`는 **지우지 않는다** — Task 7이 세부기술 탭의 [정밀 견적] 버튼에 다시 붙인다. 이 태스크 시점에는 잠시 아무도 쓰지 않는 상태가 되지만, `npm run build`는 쓰이지 않는 컴포넌트 파일 자체를 오류로 보지 않으므로 게이트는 통과한다.

- [ ] **Step 4: 버전을 올린다**

`frontend/package.json`의 `version`을 `0.30.0` → `0.31.0`으로 바꾼다(기능 추가).

- [ ] **Step 5: 게이트를 전부 돌린다**

```bash
cd frontend && ln -sfn /home/dev/code/performance-review/frontend/node_modules node_modules
npm run build && npm run lint && npm test
```
Expected: build 성공(쓰지 않는 import가 남아 있으면 여기서 잡힌다) · lint 경고 0 · 테스트 통과.

- [ ] **Step 6: 커밋**

```bash
rm -f frontend/node_modules
git add -A
git commit -m "refactor: 관리자 탭 7개 → 5개, 세부기술 관련 3개를 하나로

분석 실행·상태 / 국가 현황 / 국가 비교가 모두 세부기술 하나를 다루면서 서로 다른
축과 서로 다른 생성 방식을 갖고 있었다. 어디로 가야 하는지도, 각 탭의 일괄 버튼이
무엇을 대상으로 하는지도 알기 어려웠다.

RunDialog는 지우지 않고 남긴다 — 정밀 견적 동선이 다시 붙을 자리라 주석으로
현재 미사용임을 밝혀 둔다."
```

---

### Task 7: 셀 하나를 고르면 [정밀 견적]

**Files:**
- Modify: `frontend/src/components/RunDialog.tsx` (상단 props와 선택 폼)
- Modify: `frontend/src/components/SubfieldTab.tsx` (Task 5 결과물)

**왜 필요한가:** 설계가 정한 동선인데 Task 5까지로는 빠져 있다 — "미리보기는 셀 하나를 고른 상태에서 **[정밀 견적] 버튼으로 연다.** 상주시키지 않는다 — 호출 자체가 OpenAlex 과금이라 늘 떠 있으면 안 된다." 선택분 비용 표시(Task 3)는 과거 실적 기반 참고값이라, 처음 돌리는 국가에는 아무 숫자도 못 낸다. 그때 실제 건수를 보는 유일한 수단이 이것이다.

**Interfaces:**
- Consumes: Task 5의 `selected`·`rows`, 기존 `RunDialog`
- Produces: `RunDialog`가 `locked?: {subfieldId: number; country: string; year: number}` prop을 받아 선택 폼을 숨기고 그 대상에 고정된다.

- [ ] **Step 1: `RunDialog`에 고정 모드를 넣는다**

props 타입에 추가한다.

```tsx
  // 세부기술 탭에서 셀 하나를 고른 채 열면 그 대상에 고정한다 — 대상이 이미 정해진
  // 자리에서 세부기술·연도·국가를 다시 고르게 하면 화면이 말하는 것과 어긋난다.
  locked?: { subfieldId: number; country: string; year: number };
```

상태 초기화를 `locked`가 있으면 그 값으로 시작하게 바꾼다.

```tsx
  const [subfieldId, setSubfieldId] = useState<number | "">(locked?.subfieldId ?? "");
  const [yearFrom, setYearFrom] = useState(locked?.year ?? defaultYearFrom);
  const [yearTo, setYearTo] = useState(locked?.year ?? defaultYearTo);
  const [country, setCountry] = useState(locked?.country ?? "KR");
```

선택 폼 전체(`<div className="mt-4 flex flex-wrap items-end gap-2">` … `</div>`)를 `locked`가 없을 때만 그린다.

```tsx
      {!locked && (
        <div className="mt-4 flex flex-wrap items-end gap-2">
          {/* 기존 내용 그대로 */}
        </div>
      )}
```

- [ ] **Step 2: 세부기술 탭에 버튼을 붙인다**

`SubfieldTab.tsx`의 선택 요약 줄에 추가한다. **분석 셀 하나만 골랐을 때만** 나온다 — 견적은 한 대상에 대한 것이고, 여럿을 고르면 호출이 그만큼 과금된다.

```tsx
  const [estimating, setEstimating] = useState(false);
  const onlyAnalysis =
    payload.analyses.length === 1 && payload.comparisons.length === 0
      ? payload.analyses[0]
      : null;
```

버튼(생성 버튼 옆):

```tsx
        {onlyAnalysis && (
          <button
            type="button"
            onClick={() => setEstimating(true)}
            className="btn btn-neutral btn-sm"
          >
            정밀 견적
          </button>
        )}
```

그리고 표 위에 조건부로 `RunDialog`를 띄운다.

```tsx
      {estimating && onlyAnalysis && (
        <div className="mt-4">
          <RunDialog
            adminKey={adminKey}
            rows={rows.map((r) => ({ subfield_id: r.subfield_id, subfield_name: r.subfield_name }))}
            defaultYearFrom={year}
            defaultYearTo={year}
            subfieldsVersion={0}
            locked={{
              subfieldId: onlyAnalysis.subfield_id,
              country: onlyAnalysis.country,
              year,
            }}
            onRan={() => {
              setEstimating(false);
              load();
            }}
            onUnauthorized={onUnauthorized}
          />
          <button
            type="button"
            onClick={() => setEstimating(false)}
            className="mt-2 btn btn-neutral btn-sm"
          >
            견적 닫기
          </button>
        </div>
      )}
```

import에 `RunDialog`를 더한다.

- [ ] **Step 3: 게이트를 돌린다**

```bash
cd frontend && ln -sfn /home/dev/code/performance-review/frontend/node_modules node_modules
npm run build && npm run lint && npm test
```
Expected: 전부 통과, lint 경고 0.

- [ ] **Step 4: Task 6의 RunDialog 주석을 지운다**

Task 6에서 "현재 화면에 붙어 있지 않다"고 적은 주석은 이제 거짓이다. 지운다.

- [ ] **Step 5: 커밋**

```bash
rm -f frontend/node_modules
git add frontend/src/components/RunDialog.tsx frontend/src/components/SubfieldTab.tsx
git commit -m "feat: 셀 하나를 고르면 정밀 견적을 연다

선택분 비용 표시는 과거 실적 기반 참고값이라 처음 돌리는 국가에는 아무 숫자도 못 낸다.
그때 실제 건수를 보는 수단이 이것이다.

상주시키지 않는 이유: /admin/preview 호출 자체가 OpenAlex 과금이라 화면에 늘 떠
있으면 안 된다. 분석 셀 하나만 골랐을 때만 버튼이 나온다 — 여럿을 고르면 그만큼
과금된다."
```

---

## 2단계 완료 조건

- `cd frontend && npm run build && npm run lint && npm test` → 전부 통과, lint 경고 0
- 백엔드 무변경 (`cd backend && /home/dev/code/performance-review/backend/.venv/bin/python -m pytest -q` → 395 passed 유지)
- 관리자 탭이 5개로 줄고, 세부기술 탭에서 기술 × 국가 현황 확인과 생성이 모두 된다

## 브라우저로만 확인 가능한 것

jsdom이 없어 자동 검증할 수 없다. 배포 후 눈으로 볼 것:

1. 열 머리글 체크가 부분 상태(`indeterminate`)로 보이는가 — 일부만 골랐을 때
2. 행 펼침(`▾`)에서 과거 연도가 나오는가
3. `—` 칸이 실제로 선택되지 않는가
4. 생성 후 `skipped` 사유가 기술명과 함께 읽히는가
5. 375px에서 표가 자기 컨테이너 안에서만 가로 스크롤되는가(문서 전체가 밀리지 않는가)

## 이 계획에 없는 것

- **`RunDialog`의 나머지 정리** — Task 7이 고정 모드를 얹었지만 자유 선택 모드(세부기술·연도·국가 폼)는 이제 아무도 쓰지 않는다. 지금 지우면 Task 7의 diff가 커져 리뷰가 어려워지므로 4단계에서 죽은 분기와 함께 정리한다.
- **분야 탭 통합(3단계)** — 분야 보고서 + 로드맵. `/admin/queue`의 `field_reports`·`roadmap_checks`를 그때 쓴다.
- **구 엔드포인트 제거(4단계)** — `/admin/run` · `/admin/field-reports/run-all` · `/admin/comparisons/run-all` · `/admin/comparison-grid`.
- **`QueueComparisonIn.countries`의 `min_length=2` 완화** — 1단계 리뷰가 남긴 숙제다. 항목 하나가 잘못되면 요청 전체가 422로 죽어 "한 건이 막혀도 나머지는 큐잉한다"는 원칙과 충돌한다. 이 계획은 항상 2개 이상을 보내므로 지금 화면에서는 드러나지 않지만, 3단계에서 스키마 제약을 풀고 핸들러의 항목별 skip으로 옮겨야 한다.
