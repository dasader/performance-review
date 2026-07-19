// Report.tsx(ReportMarkdown)가 쓰는 것과 같은 remark 플러그인 조합(remarkGfm ->
// remarkCjkFriendly)으로 mdast를 만들어, 닫는 `**` 뒤에 공백 없이 한글 조사가 붙는 경우도
// 강조(strong)로 파싱되는지 검증한다. react-markdown/ReactMarkdown을 직접 렌더링하려면
// jsdom + testing-library가 필요한데, 이 리포에는 없고(다른 테스트도 순수 로직만 테스트)
// 강조 판정은 remark 파싱 단계에서 이미 끝나므로 mdast 레벨 검증으로 충분하다.
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkCjkFriendly from "remark-cjk-friendly";
import { describe, expect, it } from "vitest";
import type { Root } from "mdast";

function strongTexts(md: string): string[] {
  const processor = unified().use(remarkParse).use(remarkGfm).use(remarkCjkFriendly);
  const tree = processor.runSync(processor.parse(md)) as Root;
  const out: string[] = [];
  (function walk(node: any) {
    if (node.type === "strong") {
      out.push(node.children.map((c: any) => c.value ?? "").join(""));
    }
    for (const child of node.children ?? []) walk(child);
  })(tree);
  return out;
}

describe("ReportMarkdown 강조 파싱 (remarkGfm + remarkCjkFriendly)", () => {
  it("닫는 **에 한글 조사가 공백 없이 붙어도 강조로 인식된다", () => {
    const md =
      "**'소재-구조-공정의 다각적 엔지니어링'**과 **'지능형 최적화'**로 요약됩니다.";
    expect(strongTexts(md)).toEqual([
      "'소재-구조-공정의 다각적 엔지니어링'",
      "'지능형 최적화'",
    ]);
  });

  it("최소 재현 케이스: **강조**과 뒤", () => {
    expect(strongTexts("**강조**과 뒤")).toEqual(["강조"]);
  });

  it("일반적인 **bold** text는 그대로 동작한다", () => {
    expect(strongTexts("**bold** text")).toEqual(["bold"]);
  });

  it("remarkGfm의 표 파싱과 충돌하지 않는다", () => {
    const processor = unified().use(remarkParse).use(remarkGfm).use(remarkCjkFriendly);
    const tree = processor.runSync(processor.parse("| a | b |\n| --- | --- |\n| 1 | 2 |")) as Root;
    expect(tree.children[0].type).toBe("table");
  });
});
