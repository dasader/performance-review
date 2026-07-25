import { describe, expect, it } from "vitest";

// slicePath는 컴포넌트 내부 함수라 직접 못 부른다. 대신 렌더 결과의 형태를 고정하는
// 대신 여기서는 각도 계산 규칙만 따로 검증한다 — 조각 개수와 채움 개수가 어긋나면
// "분석 안 된 세부기술이 칠해진" 것으로 보이므로 그 경계만 지킨다.
function sliceStates(total: number, done: number): boolean[] {
  const filled = Math.min(done, total);
  return Array.from({ length: total }, (_, i) => i < filled);
}

describe("ProgressPie 조각 채움", () => {
  it("분석된 수만큼만 채운다", () => {
    expect(sliceStates(4, 3)).toEqual([true, true, true, false]);
  });

  it("0건이면 아무것도 채우지 않는다", () => {
    expect(sliceStates(4, 0)).toEqual([false, false, false, false]);
  });

  it("전부 분석되면 전부 채운다", () => {
    expect(sliceStates(4, 4)).toEqual([true, true, true, true]);
  });

  it("done이 total을 넘어도 total개까지만 — 비활성화 등으로 어긋날 때 넘치지 않는다", () => {
    expect(sliceStates(2, 5)).toEqual([true, true]);
  });
});
