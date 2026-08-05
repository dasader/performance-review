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
