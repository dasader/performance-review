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
