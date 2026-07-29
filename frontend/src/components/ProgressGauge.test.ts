import { describe, expect, it } from "vitest";

// 컴포넌트 렌더 없이(브라우저 환경이 없다) 채움 비율 계산 규칙만 고정한다.
// 여기가 어긋나면 "분석이 덜 된 분야가 다 된 것처럼" 보이므로 그 경계만 지킨다.
function pct(total: number, done: number): number {
  const filled = Math.min(Math.max(done, 0), total);
  return Math.round((filled / total) * 100);
}

describe("ProgressGauge 채움 비율", () => {
  it("분석된 만큼만 채운다", () => {
    expect(pct(4, 3)).toBe(75);
  });

  it("0건이면 채우지 않는다", () => {
    expect(pct(4, 0)).toBe(0);
  });

  it("전부 분석되면 100%", () => {
    expect(pct(5, 5)).toBe(100);
  });

  it("done이 total을 넘어도 100%를 넘지 않는다 — 비활성화 등으로 어긋날 때", () => {
    expect(pct(2, 5)).toBe(100);
  });

  it("음수 done도 0으로 눌러 막대가 뒤집히지 않게 한다", () => {
    expect(pct(4, -1)).toBe(0);
  });
});
