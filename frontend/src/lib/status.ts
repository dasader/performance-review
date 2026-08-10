// 백엔드 runner.py의 상태 어휘를 프론트에 옮겨 적은 것. 순수 데이터라 아무것도
// import하지 않는다 — 이 파일이 잎사귀여야 로직 계층(lib/selection.ts)이 통신
// 계층(api.ts)을 끌어오지 않고도 같은 상수를 쓸 수 있다. 예전에는 api.ts가 이것을
// 들고 있어서, selection.ts가 상수 하나 때문에 600줄짜리 통신 모듈에 의존했다.

// runner.py::ACTIVE_STATES와 같은 집합 — 진행 중인 분석은 batch가 이미 제출됐을 수
// 있어 삭제하면 고아 상태가 된다(백엔드도 같은 기준으로 409를 던진다). 파이썬 쪽과의
// 이중 관리는 남지만, 최소한 프론트 안에서는 한 곳에서만 정의한다.
export const ACTIVE_STATUSES = new Set(["pending", "searching", "extracting", "reducing"]);

// runner.py::STEP_LABELS와 같은 한글 라벨. 대부분의 admin 엔드포인트는 status_label을
// 함께 내려주지만 dashboard의 comparisons(세부기술 탭 국가비교 칸)·field-reports는
// 원시 상태 문자열만 주므로 화면이 짝을 맞춘다 — 그 짝을 패널마다 각자 갖고 있으면
// (예전에는 두 벌이었고 한 벌은 3개 상태만 알았다) 라벨을 고칠 때 한쪽만 바뀐다.
export const STATUS_LABEL: Record<string, string> = {
  pending: "대기 중",
  searching: "논문 검색 중",
  extracting: "성과 추출 중",
  reducing: "보고서 작성 중",
  done: "완료",
  failed: "실패",
  paused: "일시중지",
};

// StatusBadge가 찍는 점의 색. **사람이 손을 대야 하는 상태에만 찍는다** — 지금은
// failed·paused 둘뿐이고, 색도 그 둘에서만 나온다(상태 4단의 경고/위험).
// 정상은 칠하지 않는다(index.css가 .banner-ok를 두지 않은 것과 같은 규칙).
//
// done의 점을 먼저 걷어냈고(f1a8ac6), 대기·진행 중(pending·searching·extracting·
// reducing)도 같은 이유로 걷어냈다: 이 넷은 잡 루프가 알아서 넘기는 정상 경로라
// 눈길을 끌 이유가 없는데, 체크박스가 이미 붙어 있는 관리자 격자에서 무채색 점이
// 하나 더 붙으면 읽는 사람이 "무슨 표시지" 하고 한 번 멈춘다. 라벨("논문 검색 중")이
// 이미 진행 중임을 다 말하므로 점이 더하는 정보는 0이다.
// 점이 보이면 볼 것이 있다는 뜻이어야 한다 — statusDot.test.ts가 이를 고정한다.
//
// 컴포넌트가 아니라 여기 두는 이유: 상수를 .tsx에서 export하면 fast refresh가 깨진다
// (oxlint react/only-export-components). 상태 어휘는 어차피 이 잎사귀 파일이 단일 출처다.
export const STATUS_DOT_CLASS: Record<string, string> = {
  failed: "bg-danger-mark",
  paused: "bg-warning-mark",
};
