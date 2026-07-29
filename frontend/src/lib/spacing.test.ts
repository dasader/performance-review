import { describe, expect, it } from "vitest";

// 간격은 4px 그리드의 실사용 6값뿐이다: 4 · 8 · 12 · 16 · 24 · 40.
// Tailwind 스텝으로는 1 · 2 · 3 · 4 · 6 · 10 (+ 0, px = 1px 괘선).
//
// 이 테스트가 있는 이유: 간격은 규칙 중 가장 조용히 무너지는 항목이다. "여기는 좀
// 좁으니까 py-2.5로" 같은 그때그때의 판단이 한 줄씩 쌓이면 화면마다 리듬이 달라지는데,
// 리뷰에서 눈으로는 거의 안 잡힌다. 실제로 55곳이 그렇게 어긋나 있었다.
//
// **범위는 .tsx의 레이아웃 간격뿐이다.** 조작물 내부 여백(버튼 좌우 14px, 입력 좌우
// 10px 등)은 정해진 높이를 맞추기 위한 값이라 6값 밖일 수 있고, 그건 index.css의
// 컴포넌트 계약 한 곳에만 산다 — 씨앗 CSS(/home/dev/code/web-design/mockups/system.css)도
// 같은 예외를 둔다. 폭·높이(h-8 버튼, w-56 패널, h-1.5 상태 점)는 간격이 아니라
// 치수라 여기서 보지 않는다.
const ALLOWED = new Set(["0", "px", "1", "2", "3", "4", "6", "10"]);

const PROPS = "p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap-x|gap-y|space-x|space-y";
const PATTERN = new RegExp(`(?<![\\w-])(${PROPS})-(\\[[^\\]]+\\]|[\\d.]+)(?![\\w.\\[-])`, "g");

// node:fs가 아니라 Vite의 glob을 쓴다 — tsconfig.app의 types가 ["vite/client"]라
// node 타입이 없고, 앱 코드에 node 전역을 끌어들이지 않기 위해서다.
const sources = import.meta.glob("../**/*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

describe("간격 토큰", () => {
  it("훑을 파일을 실제로 찾는다", () => {
    // glob이 조용히 0개를 반환하면 위 규칙 검사가 항상 통과해 무력화된다.
    expect(Object.keys(sources).length).toBeGreaterThan(10);
  });

  it("레이아웃 간격이 6값(4/8/12/16/24/40) 안에서만 쓰인다", () => {
    const offenders: string[] = [];
    for (const [path, source] of Object.entries(sources)) {
      for (const [, prop, value] of source.matchAll(PATTERN)) {
        if (!ALLOWED.has(value)) offenders.push(`${path}: ${prop}-${value}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
