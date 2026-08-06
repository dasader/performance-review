// 관리자 세부기술 탭의 선택 상태. 컴포넌트가 아니라 여기서 판단한다 —
// 이 저장소에는 jsdom이 없어 렌더링을 자동 검증할 수 없으므로, 검증할 수 있는
// 형태(순수 함수)로 최대한 빼낸다.

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

// 진행 중 폴링 판단. 분석은 row.years의 상태(ACTIVE_STATUSES)로 알 수 있지만,
// 비교는 상태 기계가 따로 없이 row.comparisons에 "pending"으로만 큐잉된다 —
// 그 조건을 빠뜨리면 비교만 큐잉했을 때 화면이 5초 폴링을 걸지 않아 잡 루프가
// 30초 뒤 하나씩 처리하는 동안 화면이 얼어붙고 운영자가 다 됐다고 착각해 재큐잉한다.
const ACTIVE_ANALYSIS_STATUSES = new Set(["pending", "searching", "extracting", "reducing"]);

export function hasPendingWork(
  rows: {
    years: { year: number; status: string }[];
    comparisons: Record<string, Record<string, string>>;
  }[],
  year: number,
): boolean {
  return rows.some((row) => {
    if (row.years.some((c) => c.year === year && ACTIVE_ANALYSIS_STATUSES.has(c.status))) {
      return true;
    }
    const cells = row.comparisons[String(year)];
    return cells ? Object.values(cells).some((s) => s === "pending") : false;
  });
}

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
  return body;
}
