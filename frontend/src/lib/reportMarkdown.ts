// 보고서 본문에서 맨 앞 H1을 걷어낸다.
//
// 세 보고서 모두 화면이 제목을 붙이고 본문은 `## ` 절부터 시작하는 양식으로
// 통일했다(prompts.py::REPORT_FORMAT_RULES). 다만 그 규약 이전에 생성된 보고서에는
// H1이 들어 있고, 이미 LLM 비용을 들여 만든 것을 제목 하나 때문에 전부 재생성할
// 이유는 없다 — 표시 단계에서 걷어낸다.
//
// 맨 앞 H1만 지운다. 본문 중간의 `# `는 손대지 않는다(코드 블록 안 주석 등).
export function stripLeadingH1(md: string): string {
  const lines = md.split("\n");
  let i = 0;
  // 머리말이 있던 옛 보고서는 H1 앞에 한두 줄이 붙어 있다 — 빈 줄과 `---`만 건너뛴다.
  while (i < lines.length && (lines[i].trim() === "" || lines[i].trim() === "---")) i++;
  if (i < lines.length && /^#\s+\S/.test(lines[i])) {
    lines.splice(0, i + 1);
    return lines.join("\n").replace(/^[\s-]*\n/, "");
  }
  return md;
}
