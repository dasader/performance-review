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
}

// runner.py::ACTIVE_STATES와 같은 집합 — 진행 중인 분석은 batch가 이미 제출됐을 수
// 있어 삭제하면 고아 상태가 된다(백엔드도 같은 기준으로 409를 던진다). 파이썬 쪽과의
// 이중 관리는 남지만, 최소한 프론트 안에서는 한 곳에서만 정의한다.
export const ACTIVE_STATUSES = new Set(["pending", "searching", "extracting", "reducing"]);

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
}

export interface FieldSummary {
  field_name: string;
  year: number;
  subfields: SummarySubfield[];
  total_searched: number;
  total_analyzed: number;
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
  snapshot_at: string;
}

export interface Reference {
  n: number;
  title: string;
  journal: string | null;
  year: number | null;
  doi: string | null;
}

export interface Analysis {
  id: number;
  field_name: string;
  subfield_name: string;
  year: number;
  status: string;
  status_label: string;
  report_md: string | null;
  // report_md 안에서 괄호로 인용된 논문 제목이 [n] 각주로 치환된 뒤, 실제로 참조된
  // 논문만 등장 순서대로 담긴다. 치환 대상이 없으면 빈 배열.
  references: Reference[];
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
}

export interface DashboardRow {
  subfield_id: number;
  subfield_name: string;
  field_id: number;
  years: DashboardYearCell[];
}

export interface DashboardResponse {
  rows: DashboardRow[];
  budget_spent: number;
  budget_limit: number;
  default_year_range: number; // 최근 N개년(개수)이지 연도 범위가 아니다
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
  timezone: string; // 읽기 전용 — .env 전용 값
  next_run_at: string; // 스케줄 타임존(기본 KST) wall-clock, tzinfo 없음
  history: ScheduleHistoryEntry[];
}

export interface ScheduleUpdateIn {
  enabled: boolean;
  day: number;
  hour: number;
  years_back: number;
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
  estimated_cost_usd: number; // OpenAlex 검색 비용만 — 비용 내역 표시에 쓴다(RunDialog)
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

export interface RunResponse {
  queued: number[];
  blocked: { subfield_id: number; reason: string }[];
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
