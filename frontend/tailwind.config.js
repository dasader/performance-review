/** @type {import('tailwindcss').Config} */
// 토큰은 /home/dev/code/web-design/DESIGN.md §3의 값을 그대로 심은 것이다.
// 값 하나하나에 근거가 붙어 있으므로 "조금 더 밝게" 같은 취향으로 흔들지 않는다.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    // 모서리는 셋뿐이다: 면 0 · 조작물 2px · 표식 999px.
    // extend가 아니라 통째로 교체해 rounded-md/lg 같은 규격 외 값이 아예 존재하지 않게 한다.
    borderRadius: {
      none: "0",
      DEFAULT: "0",
      control: "2px", // 버튼·입력 — 손으로 누르는 물건
      full: "999px", // 칩·점·배지 — 표식
    },
    extend: {
      colors: {
        // ── 지면·면 ──
        paper: "#fafafa", // --ground  본문이 놓이는 지면
        surface: "#ffffff", // --surface 면(타일·모달) — 지면 위로 뜨는 유일한 밝기 차
        sunken: "#f4f4f5", // --sunken  눌린 면(표 머리·코드·보조 영역)

        // ── 잉크 3단 — 셋 다 지면 대비 4.5:1을 통과한다(16.97 / 10.01 / 4.63) ──
        ink: "#18181b",
        "ink-light": "#3f3f46",
        muted: "#71717a", // 하한
        // 이전 baseline의 faint(#a8a29e, 대비 2.2)는 본문 대비 미달이라 글자에서 뺐다.
        // 이름만 남기고 muted와 같은 값을 준다 — 호출부 21곳을 고치지 않고도
        // "통과 못 하는 회색은 글자에 쓰지 않는다"가 지켜진다.
        faint: "#71717a",

        // ── 괘선 2단 + 축선. 깊이는 굵기가 아니라 명도로 만든다 ──
        border: "#d4d4d8", // --rule  층 사이(섹션 경계·헤더 하단)
        "border-light": "#e4e4e7", // --hair  같은 층 안(표의 행)
        "border-strong": "#a1a1aa", // --rule-strong 축·기준선. 글자에는 쓰지 않는다

        // ── 잉크 크롬(헤더) — 본문과 색이 아니라 명도로 갈린다 ──
        chrome: "#18181b",
        "chrome-ink": "#fafafa", // 크롬 위 주 텍스트 16.97:1
        "chrome-ink-2": "#a1a1aa", // 크롬 위 보조 텍스트 6.91:1 — 밝은 지면 위였다면 금지지만
        //                            잉크 위에서는 통과한다. 금지 대상은 색이 아니라 조합이다.
        "chrome-rule": "#3f3f46",

        // ── accent는 무채색이다 ──
        // UI 크롬(헤더·제목·링크·포커스)에 채도를 두지 않는다. 화면에 색이 보이면 그건 정보다.
        // 이름을 남긴 이유는 호출부를 건드리지 않기 위해서다.
        accent: { DEFAULT: "#18181b", light: "#f4f4f5", border: "#d4d4d8" },

        // ── 상태 — 마크용과 글자용이 다르다. 마크 색을 글자에 쓰면 대비가 안 나온다 ──
        positive: "#006300", // 7.54:1
        warning: "#8a5a00", // 5.93:1
        danger: "#a11c1c", // 7.78:1
        "positive-mark": "#0ca30c",
        "warning-mark": "#fab219",
        "danger-mark": "#d03b3b",
        "positive-bg": "#eef8ee",
        "warning-bg": "#fdf6e6",
        "danger-bg": "#fdefef",

        // ── 데이터 계열 8슬롯 — CVD 판별·명도 대역·면 대비를 통과한 고정 순서.
        //    1→8 순서대로 쓰고 절대 순환시키지 않는다. 색은 항목을 따르지 순위를 따르지 않는다.
        d1: "#2a78d6",
        d2: "#eb6834",
        d3: "#1baf7a",
        d4: "#eda100",
        d5: "#e87ba4",
        d6: "#008300",
        d7: "#4a3aa7",
        d8: "#e34948",
      },
      fontFamily: {
        // 폰트는 2벌뿐이다. 별도 디스플레이 폰트(Bricolage)를 폐지했다 — 라틴 전용이라
        // 한글 제목에서 반드시 폰트가 갈린다. 제목은 굵기·크기·자간으로 만든다.
        // fallback 체인의 모든 단계가 한글을 커버한다(system-ui·-apple-system은 안 덮으므로 뺐다).
        sans: [
          "Pretendard Variable",
          "Pretendard",
          "Apple SD Gothic Neo",
          "Noto Sans KR",
          "Malgun Gothic",
          "맑은 고딕",
          "sans-serif",
        ],
        // mono는 한글이 섞인 문자열에 쓰지 않는다 — 코드·DOI·해시·버전 전용.
        // 숫자 정렬은 mono가 아니라 tabular-nums(body 전역)로 해결한다.
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
      },
      // 크기는 역할이 정한다. 토큰 6개(11/12/14/16/20/26) 밖의 임의 크기는 쓰지 않는다.
      // Tailwind 기본 계단(18/24/30/36px)을 이 6값으로 눌러 담아, 화면마다 제목 크기가
      // 흔들리는 경로 자체를 없앤다. 밀도는 글자 크기가 아니라 행 높이와 여백으로 만든다.
      fontSize: {
        eyebrow: ["0.6875rem", { lineHeight: "1.45" }], // 11 라벨·범례·표 머리. 하한
        xs: ["0.75rem", { lineHeight: "1.5" }], // 12 캡션·도움말·보조 수치
        sm: ["0.875rem", { lineHeight: "1.7" }], // 14 본문·표 셀·폼 입력. 기본
        base: ["1rem", { lineHeight: "1.4" }], // 16 카드·섹션 제목
        lg: ["1.25rem", { lineHeight: "1.4" }], // 20 페이지 내 대구획
        xl: ["1.25rem", { lineHeight: "1.4" }], // 20 (= lg. 같은 역할은 같은 크기다)
        "2xl": ["1.625rem", { lineHeight: "1.25" }], // 26 페이지 제목
        "3xl": ["1.625rem", { lineHeight: "1.25" }], // 26
        "4xl": ["1.625rem", { lineHeight: "1.25" }], // 26
      },
      maxWidth: {
        page: "1180px", // --maxw. 본문 단이 약 890px = 한글 64자쯤이라 읽기 폭이 이미 묶인다
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
