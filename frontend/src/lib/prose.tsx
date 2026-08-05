import type { Components } from "react-markdown";

// h1(페이지 제목) > h2(섹션 제목) > h3(하위 제목) > 본문 위계를 마크다운 헤딩(##, ###)에도
// 강제한다. prose 기본값에 맡기면 h3가 text-sm(본문 text-base보다 작음)으로 떨어져 위계가
// 역전된다 — 마크다운 헤딩은 항상 본문보다 커야 하고, 같은 레벨은 항상 같은 크기여야 한다.
// prose-h2는 StatsPanel/References의 네이티브 h2("기본 통계", "참고문헌")와 같은 text-xl(20px)로
// 맞춰 "섹션 제목" 레벨을 통일한다. [&>*:first-child]:mt-0 은 (구체적 헤딩 레벨과 무관하게)
// 블록의 첫 자식 위쪽 여백만 지워 구분선 바로 아래가 뜨지 않게 하면서, 두 번째 이후 헤딩의
// 여백은 살려 섹션 경계가 보이게 한다.
//
// ★ h2에서 색을 뺐다. 이전에는 h2만 남색(accent)이었는데, UI 크롬에 채도를 두면
// "화면에 색이 보이면 그건 정보다"라는 규칙이 무너진다. 대신 아래 괘선으로 섹션 경계를
// 만든다 — 위계도 깊이도 색이 아니라 선과 명도로 말한다. 크기·굵기 차이는 그대로라
// 색으로 위계를 못 읽는 경우에도 구분된다(원래의 접근성 근거도 그대로 유지된다).
// h3는 text-lg(20px)에서 text-base(16px)로 내렸다 — h2와 같은 크기였던 탓에 두 레벨이
// 굵기로만 갈렸고, 활자 토큰상 "섹션 제목"과 "카드·하위 제목"은 다른 크기를 갖는다.
//
// 본문에 별도 읽기 폭 제한을 걸지 않는다. 폭 제한(한 줄 40자)은 장문용 규칙인데 이
// 보고서는 문단이 3~5줄이고 그 사이로 표·그림이 계속 끼어든다 — 폭을 깎으면 글은 단의
// 3분의 2만 쓰는데 바로 아래 표는 꽉 차서 글이 잘린 것처럼 보인다. 대신 줄이 길어진
// 만큼 행간을 넓혀(1.85) 다음 줄 첫 글자를 놓치지 않게 한다. 폭 대신 행간으로 푼다.
//
// 세부기술 보고서(Report)와 분야 보고서(FieldDetail)가 같은 LLM 프롬프트 계열에서 나온
// 같은 형태의 마크다운이라 두 화면이 이 상수를 공유한다.
// @tailwindcss/typography의 기본 크기는 전부 em 상대값이다(h1 2.25em=36px, h2 1.5em,
// blockquote 1.25em, code .875em…). 그대로 두면 활자 토큰 6개 밖의 크기가 보고서 본문
// 안에서만 조용히 살아난다 — 컴파일된 CSS를 재서 실제로 그렇다는 것을 확인했다.
// h2·h3뿐 아니라 h1·인용·코드까지 토큰으로 못 박는 이유다.
const PROSE_HEADING_CLASSES =
  "prose-headings:tracking-tight prose-headings:text-ink " +
  "[&>*:first-child]:mt-0 " +
  "prose-h1:text-2xl prose-h1:font-extrabold prose-h1:mb-4 " +
  "prose-h2:text-xl prose-h2:font-bold prose-h2:mt-10 prose-h2:mb-4 " +
  "prose-h2:border-b prose-h2:border-border prose-h2:pb-2 " +
  "prose-h3:text-base prose-h3:font-bold prose-h3:mt-6 prose-h3:mb-2 " +
  "prose-h4:text-base prose-h4:font-semibold " +
  "prose-blockquote:text-sm prose-blockquote:border-border prose-blockquote:not-italic " +
  "prose-code:text-xs prose-code:bg-sunken prose-code:font-normal " +
  "prose-a:text-ink prose-a:underline prose-a:decoration-border-strong prose-a:underline-offset-2 hover:prose-a:decoration-ink";

// 마크다운 표도 화면의 다른 표와 같은 계약을 따른다: 머리행은 눌린 면(--sunken) +
// eyebrow, 세로 괘선은 긋지 않는다(열 정렬이 이미 열을 가른다), 행 구분은 hair.
// 이 지정이 없으면 prose 기본값이 머리행을 그냥 굵은 글씨로만 두어, 같은 화면 안에서
// 네이티브 표(세부기술별 분석 현황)와 마크다운 표가 서로 다른 모양이 된다.
const PROSE_TABLE_CLASSES =
  "prose-table:text-sm prose-thead:border-border " +
  "prose-th:bg-sunken prose-th:px-3 prose-th:py-1 prose-th:text-eyebrow prose-th:font-bold " +
  "prose-th:uppercase prose-th:tracking-[0.09em] prose-th:text-muted prose-th:leading-snug " +
  // 표는 본문의 행간(leading-[1.85])을 물려받으면 안 된다. 그 값은 한국어 문단을
  // 읽기 위한 것인데, 표는 눈이 세로로 훑는 물건이라 같은 행간이면 20행짜리 표가
  // 화면 두 장으로 퍼져 한눈에 안 들어온다(사용자 지적). 셀 여백도 8px→4px.
  "prose-td:px-3 prose-td:py-1 prose-td:align-top prose-td:leading-snug";

export const PROSE_CLASSES = `prose prose-neutral max-w-none leading-[1.85] ${PROSE_TABLE_CLASSES} ${PROSE_HEADING_CLASSES}`;

// 마크다운 표는 열 수를 우리가 정하지 못한다(LLM이 만든다) — 375px에서 3열짜리 표가
// 본문 폭을 넘겨 문서 전체를 가로로 밀었다. 화면의 다른 표와 같이 자기 컨테이너 안에서만
// 스크롤하도록 감싼다. <table>에 직접 display:block을 주는 흔한 우회법은 쓰지 않는다 —
// 그러면 thead의 table-header-group이 깨져 인쇄에서 머리행이 페이지마다 반복되지 않는다.
export const MARKDOWN_COMPONENTS: Components = {
  table({ children, node: _node, ...props }) {
    return (
      <div className="table-scroll">
        <table {...props}>{children}</table>
      </div>
    );
  },
};

