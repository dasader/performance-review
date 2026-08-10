import { describe, expect, it } from "vitest";

import { ACTIVE_STATUSES, STATUS_DOT_CLASS, STATUS_LABEL } from "./status";

// 상태 점은 "사람이 손을 대야 하는 상태"에만 찍는다는 규칙을 고정한다.
// 점이 조용히 되살아난 적이 두 번 있다 — done(f1a8ac6)과 진행 중 4종. 라벨이 이미
// 상태를 다 말하므로 정상 경로의 점은 정보를 0만큼 더하면서 격자에서 눈에만 걸린다.
describe("상태 점", () => {
  it("정상 경로(대기·진행 중·완료)에는 점을 찍지 않는다", () => {
    for (const status of [...ACTIVE_STATUSES, "done"]) {
      expect(
        STATUS_DOT_CLASS[status],
        `${status}(${STATUS_LABEL[status]})에 점이 붙었다`,
      ).toBeUndefined();
    }
  });

  it("손을 대야 하는 상태(실패·일시중지)에만 점을 찍는다", () => {
    expect(Object.keys(STATUS_DOT_CLASS).sort()).toEqual(["failed", "paused"]);
  });
});
