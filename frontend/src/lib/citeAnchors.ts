// 각주 링크([\[1\]](#ref-1)) 중 "그 번호의 첫 인용"이 md 안에서 시작하는 offset을 모아둔다.
// 렌더 도중 Set에 누적하는 방식은 StrictMode의 이중 렌더에서 두 번째 렌더가 전부 "이미 본 것"이
// 되어 id가 하나도 안 붙는다 — md만 보고 결정하면 몇 번을 렌더하든 같은 결과가 나온다.
export function firstCiteOffsets(md: string): Map<string, number> {
  const first = new Map<string, number>();
  for (const m of md.matchAll(/\[.*?\]\(#ref-(\d+)\)/g)) {
    if (!first.has(m[1])) first.set(m[1], m.index);
  }
  return first;
}
