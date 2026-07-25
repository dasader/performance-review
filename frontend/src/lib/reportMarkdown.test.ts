import { describe, expect, it } from "vitest";
import { stripLeadingH1 } from "./reportMarkdown";

describe("stripLeadingH1", () => {
  it("맨 앞 H1을 지운다 — 화면 제목과 중복되기 때문", () => {
    expect(stripLeadingH1("# 반도체 2026년 성과 분석 보고서\n\n## 분야 개괄\n본문")).toBe(
      "## 분야 개괄\n본문",
    );
  });

  it("H1 앞의 빈 줄과 구분선을 건너뛴다 — 옛 보고서에 머리말 흔적이 남아 있다", () => {
    expect(stripLeadingH1("\n---\n\n# 제목\n\n## 절\n본문")).toBe("## 절\n본문");
  });

  it("H1이 없으면 그대로 둔다 — 새 양식의 보고서", () => {
    const md = "## 1. 점검 개요\n본문";
    expect(stripLeadingH1(md)).toBe(md);
  });

  it("본문 중간의 H1은 건드리지 않는다", () => {
    const md = "## 절\n본문\n\n# 중간 제목\n더";
    expect(stripLeadingH1(md)).toBe(md);
  });

  it("H2로 시작하면 지우지 않는다 — H1만 대상", () => {
    expect(stripLeadingH1("## 분야 종합\n본문")).toBe("## 분야 종합\n본문");
  });
});
