// h1(페이지 제목) > h2(섹션 제목) > h3(하위 제목) > 본문 위계를 마크다운 헤딩(##, ###)에도
// 강제한다. prose 기본값에 맡기면 h3가 text-sm(본문 text-base보다 작음)으로 떨어져 위계가
// 역전된다 — 마크다운 헤딩은 항상 본문보다 커야 하고, 같은 레벨은 항상 같은 크기여야 한다.
// prose-h2는 StatsPanel/References의 네이티브 h2("기본 통계", "참고문헌")와 같은 text-xl로
// 맞춰 "섹션 제목" 레벨을 통일한다. [&>*:first-child]:mt-0 은 (구체적 헤딩 레벨과 무관하게)
// 블록의 첫 자식 위쪽 여백만 지워 구분선 바로 아래가 뜨지 않게 하면서, 두 번째 이후 헤딩의
// 여백은 살려 섹션 경계가 보이게 한다.
//
// h2("섹션 제목" 레벨)만 accent 색을 준다 — 모든 레벨에 색을 주면 구분이 사라지므로 h3/본문은
// prose-headings의 기본 text-ink를 그대로 물려받는다. 크기·굵기 차이는 그대로 유지되므로 색맹
// 등 색만으로 위계를 못 읽는 경우에도 구분 가능하다(접근성). prose-a는 각주 링크([1] →
// 참고문헌) 색을 References의 DOI 링크와 통일한다.
//
// 세부기술 보고서(Report)와 분야 보고서(FieldDetail)가 같은 LLM 프롬프트 계열에서 나온
// 같은 형태의 마크다운이라 두 화면이 이 상수를 공유한다.
const PROSE_HEADING_CLASSES =
  "prose-headings:font-display prose-headings:tracking-tight prose-headings:text-ink " +
  "[&>*:first-child]:mt-0 " +
  "prose-h2:text-xl prose-h2:font-bold prose-h2:mt-12 prose-h2:mb-4 prose-h2:text-accent " +
  "prose-h3:text-lg prose-h3:font-bold prose-h3:mt-8 prose-h3:mb-2 " +
  "prose-a:text-accent prose-a:underline prose-a:decoration-border prose-a:underline-offset-2 hover:prose-a:decoration-accent";

export const PROSE_CLASSES = `prose prose-neutral max-w-none prose-table:text-sm ${PROSE_HEADING_CLASSES}`;
