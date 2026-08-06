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
