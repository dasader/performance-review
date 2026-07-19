import { describe, expect, it } from "vitest";
import { firstCiteOffsets } from "./citeAnchors";

describe("firstCiteOffsets", () => {
  it("같은 번호가 여러 번 인용되면 첫 등장 offset만 남긴다", () => {
    const md = "가나[\\[1\\]](#ref-1) 다라[\\[2\\]](#ref-2) 마바[\\[1\\]](#ref-1)";
    const first = firstCiteOffsets(md);
    expect(first.get("1")).toBe(md.indexOf("[\\[1\\]]"));
    expect(first.get("2")).toBe(md.indexOf("[\\[2\\]]"));
  });

  it("각주가 아닌 링크는 무시한다", () => {
    expect(firstCiteOffsets("[doi](https://doi.org/10.1/x)").size).toBe(0);
  });
});
