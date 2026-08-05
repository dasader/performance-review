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
