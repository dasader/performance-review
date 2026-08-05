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
