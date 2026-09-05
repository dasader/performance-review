import type { QueueRequestBody } from "./lib/selection";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

const NETWORK_ERROR_MESSAGE = "서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.";
const GENERIC_ERROR_MESSAGE = "요청에 실패했습니다.";

// 관리자 화면은 401을 특별 취급해야 한다(저장된 키를 지우고 인증 화면으로 되돌림).
// status를 들고 다니는 에러 타입 하나만 있으면 모든 admin 호출부가 같은 방식으로 판별할 수 있다.
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

// fetch() 자체가 reject되는 경우(백엔드 다운, DNS 실패 등)는 브라우저가 영어 메시지를
// 던지므로 여기서 한국어 메시지로 변환한다. get/post/put/del이 공유하는 지점이라 한 번만 처리하면 된다.
async function doFetch(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${BASE}${path}`, init);
  } catch {
    throw new Error(NETWORK_ERROR_MESSAGE);
  }
}

// 응답 본문이 JSON이 아닌 경우(HTML 에러 페이지 등)도 한국어 기본 메시지로 떨어지게 한다.
async function parseJson<T>(res: Response): Promise<T> {
  try {
    return (await res.json()) as T;
  } catch {
    throw new Error(GENERIC_ERROR_MESSAGE);
  }
}

function adminHeaders(adminKey?: string): HeadersInit {
  return adminKey ? { "X-Admin-Key": adminKey } : {};
}

// FastAPI/Pydantic의 표준 422 응답은 detail이 문자열이 아니라
// [{loc, msg, type}, ...] 배열이다. 그대로 메시지로 쓰면 "[object Object]"가 뜨므로
// 필드명(loc의 마지막 요소)과 msg를 사람이 읽을 수 있는 문장으로 합친다.
function formatDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const lines = detail
      .map((item) => {
        if (!item || typeof item !== "object") return null;
        const loc = Array.isArray((item as { loc?: unknown }).loc) ? (item as { loc: unknown[] }).loc : [];
        const field = loc.length > 0 ? String(loc[loc.length - 1]) : null;
        const msg = typeof (item as { msg?: unknown }).msg === "string" ? (item as { msg: string }).msg : null;
        if (field && msg) return `${field}: ${msg}`;
        return msg;
      })
      .filter((s): s is string => Boolean(s));
    if (lines.length > 0) return lines.join(" / ");
  }
  return GENERIC_ERROR_MESSAGE;
}

async function throwOnError(res: Response): Promise<void> {
  if (res.ok) return;
  const body = await res.json().catch(() => null);
  throw new ApiError(formatDetail(body?.detail), res.status);
}

export async function get<T>(path: string, adminKey?: string): Promise<T> {
  const res = await doFetch(path, { headers: adminHeaders(adminKey) });
  await throwOnError(res);
  return parseJson<T>(res);
}

export async function post<T>(path: string, body: unknown, adminKey?: string): Promise<T> {
  const res = await doFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...adminHeaders(adminKey) },
    body: JSON.stringify(body),
  });
  await throwOnError(res);
  return parseJson<T>(res);
}

export async function put<T>(path: string, body: unknown, adminKey: string): Promise<T> {
  const res = await doFetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...adminHeaders(adminKey) },
    body: JSON.stringify(body),
  });
  await throwOnError(res);
  return parseJson<T>(res);
}

export async function del(path: string, adminKey: string): Promise<void> {
  const res = await doFetch(path, { method: "DELETE", headers: adminHeaders(adminKey) });
  await throwOnError(res);
}

export interface Subfield {
  id: number;
  name: string;
  active: boolean;
}

export interface Field {
  id: number;
  name: string;
  slug: string;
  subfields: Subfield[];
  // 랜딩 화면 진행 파이용 — 서버 기준 "올해"와 그 해에 분석이 끝난 활성 세부기술 수.
  current_year: number;
  current_year_done: number;
}

// 상태 어휘(ACTIVE_STATUSES·STATUS_LABEL)는 lib/status.ts에 있다 — 아무것도
// import하지 않는 잎사귀라야 로직 계층이 이 통신 모듈을 끌어오지 않는다.

export interface YearRow {
  year: number;
  subfield_count: number;
  done_count: number;
}

export interface SummarySubfield {
  subfield_id: number;
  subfield_name: string;
  analysis_id: number | null;
  status: string;
  status_label: string;
  searched_count: number;
  analyzed_count: number;
  // 이 세부기술·연도에 완료된 분석이 있는 국가 코드들. 비어 있으면 아직 아무 나라도
  // 완료되지 않은 것 — 표는 결측 기호(—)로 표시한다.
  countries: string[];
}

// 보고서 상단 국가 줄용 — 이 세부기술·연도에 실제로 완료된 국가·비교만 담겨 온다
// (없는 조합은 백엔드가 아예 뺀다. 공개 화면은 미보유를 드러내지 않는다).
export interface Availability {
  countries: string[];
  comparisons: { countries: string[]; label: string }[];
}

export function getAvailability(subfieldId: number, year: number) {
  return get<Availability>(`/subfields/${subfieldId}/availability?year=${year}`);
}

export interface FieldSummary {
  field_name: string;
  year: number;
  subfields: SummarySubfield[];
  total_searched: number;
  total_analyzed: number;
}

// 분야(대분류) 보고서 — 세부기술 보고서들을 LLM 1콜로 합성한 결과. 생성은 관리자만
// 할 수 있고(POST /admin/fields/{id}/report), 조회는 공개다. 아직 생성 전이면 404.
export interface FieldReport {
  field_id: number;
  year: number;
  // pending(큐잉됨) | done | failed. "생성"은 즉시 실행이 아니라 큐잉이라, 화면은
  // status를 폴링해 done될 때 갱신한다.
  status: "pending" | "done" | "failed";
  error: string | null;
  report_md: string;
  // 합성에 들어간 세부기술 보고서 수 / 지금 완성돼 있는 수. 다르면 stale.
  source_count: number;
  current_count: number;
  stale: boolean;
  generated_at: string | null;
}

// 분야 종합 보고서 전용 페이지의 "세부기술 보고서 포함" 토글용. report_md는 세부기술
// 보고서 화면과 똑같이 각주 치환이 적용된 상태로 온다(논문 제목 → [n]), references는
// 그 각주가 가리키는 목록이다.
export interface SubfieldReportBody {
  name: string;
  report_md: string;
  references: Reference[];
}
export interface SubfieldReportsResponse {
  field_id: number;
  year: number;
  reports: SubfieldReportBody[];
}

export interface CitationStats {
  median: number;
  p90: number;
  total: number;
}

export interface TopCited {
  title: string;
  citations: number;
  year: number | null;
  journal: string | null;
  doi: string | null;
}

export interface MetricStat {
  name: string;
  unit: string;
  count: number;
  // 분포는 최소~중앙값~최대 범위다. p90은 쓰지 않는다 — 표본이 작으면 최대값과
  // 같은 값이 되어 같은 숫자가 두 열에 나왔다(stats.aggregate_metrics 주석 참고).
  min: number;
  median: number;
  max: number;
}

export interface Stats {
  searched_count: number;
  analyzed_count: number;
  no_abstract_count: number;
  no_country_count: number;
  no_year_count: number;
  no_journal_count: number;
  by_year: Record<string, number>;
  by_source: Record<string, number>;
  top_institutions: [string, number][];
  top_journals: [string, number][];
  top_authors: [string, number][];
  intl_collab_ratio: number;
  top_partner_countries: [string, number][];
  citations: CitationStats;
  top_cited: TopCited[];
  by_achievement_type: Record<string, number>;
  // 과거 분석의 stats_json에는 없다 — 반드시 선택 필드로 둔다.
  metrics_total?: number;
  metrics_parsed?: number;
  metrics_papers?: number;
  metrics_unique?: number;
  top_metrics?: MetricStat[];
  snapshot_at: string;
}

export interface Reference {
  n: number;
  title: string;
  journal: string | null;
  year: number | null;
  doi: string | null;
}

export interface ReportSection {
  name: string;
  body_md: string;
}

export interface Analysis {
  id: number;
  field_id: number;
  field_name: string;
  subfield_id: number;
  subfield_name: string;
  year: number;
  // 이 세부기술에 분석 행이 있는 연도 전부(오름차순). 보고서 화면의 이전/다음 연도
  // 이동에 쓴다 — 없는 연도로 보내면 404 화면이 뜨므로 목록에 있는 연도만 링크한다.
  years: number[];
  status: string;
  status_label: string;
  report_md: string | null;
  // report_md 안에서 괄호로 인용된 논문 제목이 [n] 각주로 치환된 뒤, 실제로 참조된
  // 논문만 등장 순서대로 담긴다. 치환 대상이 없으면 빈 배열.
  references: Reference[];
  // 3단 reduce의 성과유형별 상세. 각주 번호는 report_md와 같은 체계를 쓴다.
  // 단일 reduce 분석과 재생성 전 기존 분석은 빈 배열이다.
  sections?: ReportSection[];
  // 분석 대상 국가(ISO 3166-1 alpha-2)와 그 한글 이름. 같은 세부기술·연도라도
  // 국가가 다르면 다른 분석이다.
  country: string;
  country_name: string;
  // 미완료 상태에서는 null이 아니라 백엔드 stats_json 컬럼의 default인 빈 객체({})가 온다.
  stats: Stats | Record<string, never>;
  searched_count: number;
  analyzed_count: number;
  snapshot_at: string | null;
  error: string | null;
}

// ── 관리자 화면 (Task 13) ──

export interface AdminSubfield {
  id: number;
  field_id: number;
  name: string;
  query: string;
  query_kci: string | null;
  active: boolean;
}

export interface DashboardYearCell {
  analysis_id: number;
  year: number;
  status: string;
  status_label: string;
  searched_count: number;
  analyzed_count: number;
  snapshot_at: string | null;
  stale: boolean;
  error: string | null;
  // 같은 세부기술·연도라도 국가가 다르면 다른 분석이다(analyses의 유일키에 country가 있다).
  country: string;
}

export interface DashboardRow {
  subfield_id: number;
  subfield_name: string;
  field_id: number;
  // false면 비활성 세부기술 — 행은 보여주되(운영자가 존재를 알아야 함) 선택 후보에서는
  // 뺀다. runner.enqueue는 이 플래그를 보지 않으므로 프론트가 유일한 가드다.
  active: boolean;
  years: DashboardYearCell[];
  // 연도(문자열) → 정렬된 콤마 국가키 → 상태. 상태가 "in_multi"면 그 1:1이 다국
  // 비교 안에 이미 들어 있다는 뜻이다(따로 만들 필요가 없다).
  comparisons: Record<string, Record<string, string>>;
}

export interface DashboardResponse {
  rows: DashboardRow[];
  budget_spent: number;
  budget_limit: number;
}

// POST /admin/queue의 응답. skipped는 조용히 건너뛰지 않기 위한 것이라
// 화면이 사유를 그대로 보여준다 — 문자열을 매칭해 분기하지 말 것.
export interface QueueResponse {
  queued: {
    analyses: number;
    comparisons: number;
    field_reports: number;
    roadmap_checks: number;
  };
  skipped: {
    kind: "analysis" | "comparison" | "field_report" | "roadmap_check";
    subfield_id?: number;
    field_id?: number;
    country?: string;
    countries?: string[];
    reason: string;
  }[];
}

export function queueAll(body: QueueRequestBody, adminKey: string) {
  return post<QueueResponse>("/admin/queue", body, adminKey);
}

// ── 분야 보고서 탭 ──

export interface FieldReportCell {
  status: "pending" | "done" | "failed";
  source_count: number;
  generated_at: string | null;
  error: string | null;
}

export interface FieldReportRow {
  field_id: number;
  field_name: string;
  // 등록된 로드맵의 판본과 목표 수. 미등록이면 null — 빈 문자열로 내면
  // "등록됐는데 판본명이 비었다"와 구별되지 않는다.
  roadmap: { version_label: string; goal_count: number } | null;
  report: FieldReportCell | null;
  roadmap_check: FieldReportCell | null;
}

export interface FieldReportsResponse {
  year: number;
  rows: FieldReportRow[];
}

// 로드맵 원문. 미등록 분야도 404가 아니라 빈 값이 온다 — 편집 폼이 그대로 새 입력을 받는다.
export interface RoadmapDoc {
  version_label: string;
  content_md: string;
  // 저장된 원문에서 센 목표 행 수. 0이면 표 형식이 아니라 전수 점검을 강제할 수 없다.
  goal_count: number;
  updated_at: string | null;
}

export function getRoadmap(fieldId: number, adminKey: string) {
  return get<RoadmapDoc>(`/admin/fields/${fieldId}/roadmap`, adminKey);
}

export function putRoadmap(
  fieldId: number,
  body: { version_label: string; content_md: string },
  adminKey: string,
) {
  return put<{ goal_count: number }>(`/admin/fields/${fieldId}/roadmap`, body, adminKey);
}

export function deleteRoadmap(fieldId: number, adminKey: string) {
  return del(`/admin/fields/${fieldId}/roadmap`, adminKey);
}

// ── 자동 분석 스케줄 (관리자 화면 스케줄 설정 카드) ──

export interface ScheduleHistoryEntry {
  run_month: string; // 정기 실행: "YYYY-MM". 수동 실행: "YYYY-MM-manual-...".
  ran_at: string; // 스케줄 타임존(기본 KST) wall-clock, tzinfo 없음
  trigger: "scheduled" | "manual";
  queued_count: number;
  done_count: number;
  failed_count: number;
  paused_count: number;
  in_progress_count: number;
  // false면 failed/paused/in_progress는 이후 같은 trigger의 실행에 상태가 덮어써져
  // 근사할 수 없어 0으로 채워진 값이다(백엔드 schedule_history 주석 참고).
  is_current_snapshot: boolean;
}

export interface ScheduleInfo {
  enabled: boolean;
  day: number;
  hour: number;
  years_back: number;
  // 스케줄러가 돌 국가. 콤마 구분("KR,US,CN"). 국가마다 검색·추출이 따로 돌아 비용이 곱해진다.
  countries: string;
  // 대상국 분석이 전부 done이 되면 국가 비교(다국 1건)를 자동 큐잉한다.
  auto_comparison: boolean;
  timezone: string; // 읽기 전용 — .env 전용 값
  next_run_at: string; // 스케줄 타임존(기본 KST) wall-clock, tzinfo 없음
  history: ScheduleHistoryEntry[];
}

export interface ScheduleUpdateIn {
  enabled: boolean;
  day: number;
  hour: number;
  years_back: number;
  countries: string;
  auto_comparison: boolean;
}

export interface RunNowResponse {
  queued_count: number;
}

export interface PreviewSample {
  title: string;
  // 백엔드가 OpenAlex publication_year를 그대로 통과시키므로 null일 수 있다(openalex.py::_parse_work).
  year: number | null;
  journal: string | null;
  has_abstract: boolean;
}

export interface PreviewResponse {
  openalex_count: number;
  // KCI는 count 전용 API가 없어 표본 상한(20건)까지만 센 값이다. openalex_count(정확한 전체
  // 건수)와 성격이 다르므로 절대 같은 라벨로 섞어 보여주지 않는다.
  kci_sample_count: number;
  kci_sample_truncated: boolean;
  samples: PreviewSample[];
  estimated_pages: number;
  estimated_cost_usd: number; // OpenAlex 검색 비용만 — 비용 내역 표시에 쓴다(EstimatePanel)
  // 아래 세 값은 모두 추정치다. estimated_papers_to_extract는 검색 없이는 캐시 히트를
  // 뺄 수 없어 상한선 성격(min(openalex_count, max_papers))으로 계산된다.
  estimated_papers_to_extract: number;
  estimated_llm_cost_usd: number;
  estimated_total_cost_usd: number;
  budget_spent: number;
  budget_limit: number;
  over_limit: boolean;
  max_papers: number;
}

// ── 푸터: 사이트 정보 · 방문자 통계 ──

export interface SiteInfo {
  // 비어 있으면 프론트가 window.location.host로 대체한다.
  domain: string;
  // 백엔드가 자기 API 버전을 함께 내려주지만, 화면 표기는 프론트 package.json이
  // 단일 출처다(Footer는 이 필드 대신 __APP_VERSION__을 쓴다).
  version: string;
}

export interface DailyVisitorCount {
  date: string;
  count: number;
}

export interface VisitorStats {
  today: number;
  this_week: number;
  daily: DailyVisitorCount[];
}

// 로드맵 이행 점검 — 로드맵의 단계별 목표를 전수로 대조한 보고서.
// 분야 종합 보고서(FieldReport)와 별개다: 로드맵이 없는 분야도 종합 보고서는 쓸 수
// 있어야 하고, 로드맵만 개정됐을 때 점검만 다시 돌릴 수 있어야 한다.
export interface RoadmapCheck {
  field_id: number;
  year: number;
  status: "pending" | "done" | "failed";
  error: string | null;
  report_md: string;
  source_count: number;
  current_count: number;
  // 로드맵에서 코드로 센 목표 행 수 / 생성된 보고서에서 실제로 점검된 행 수.
  goal_count: number;
  checked_count: number;
  // 둘이 다르면 모델이 목표를 뭉뚱그려 일부가 빠졌다는 뜻 — "빠짐없이 점검했다"로
  // 읽히면 안 되므로 화면에서 경고한다.
  incomplete: boolean;
  roadmap_version: string;
  // 세부기술 보고서가 늘었거나 로드맵 판본이 바뀌면 true.
  stale: boolean;
  generated_at: string | null;
}

// 국가 비교 보고서. FieldReport와 같은 큐잉 규약 — "생성"은 즉시 실행이 아니라
// 큐잉이라 화면이 status를 폴링한다.
export interface Comparison {
  subfield_id: number;
  subfield_name: string | null;
  year: number;
  // 정렬된 국가 코드와 그 한글 이름(같은 순서).
  countries: string[];
  country_names: string[];
  status: "pending" | "done" | "failed";
  error: string | null;
  report_md: string;
  // 합성에 들어간 국가 수.
  source_count: number;
  generated_at: string | null;
  // 3개국 이상 비교일 때의 쌍별(기준국 vs 각 상대국) 원본 보고서. 2개국 비교는
  // 그 자체가 유일한 쌍이라 report_md와 중복이므로 빈 배열로 온다.
  sections: { name: string; body: string }[];
}

export function getComparison(subfieldId: number, year: number, countries: string[]) {
  return get<Comparison>(
    `/subfields/${subfieldId}/comparison?year=${year}&countries=${countries.join(",")}`,
  );
}


// 지표 표의 한 행 뒤에 있는 논문 목록(§10-1). 이상값을 기계적으로 거를 수 없어
// — 같은 지표명 아래 다른 물리량이 섞인다 — 지우는 대신 확인 가능하게 만든 것이다.
export interface MetricPaper {
  value: number;
  raw: string | null;
  // 측정 대상. "97.3%가 왜 여기 있지"의 답이 대개 여기 있다(예: "쿡 컨버터").
  target: string | null;
  label: string;
  title: string | null;
  journal: string | null;
  year: number | null;
  doi: string | null;
}

export function getMetricPapers(analysisId: number, name: string, unit: string) {
  const q = new URLSearchParams({ name, unit });
  return get<{ name: string; unit: string; count: number; rows: MetricPaper[] }>(
    `/analyses/${analysisId}/metrics?${q}`,
  );
}
