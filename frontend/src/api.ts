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

async function throwOnError(res: Response): Promise<void> {
  if (res.ok) return;
  const body = await res.json().catch(() => null);
  throw new ApiError(body?.detail ?? GENERIC_ERROR_MESSAGE, res.status);
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

export interface Analysis {
  id: number;
  field_name: string;
  subfield_name: string;
  year: number;
  status: string;
  status_label: string;
  report_md: string | null;
  // 미완료 상태에서는 null이 아니라 백엔드 stats_json 컬럼의 default인 빈 객체({})가 온다.
  stats: Stats | Record<string, never>;
  searched_count: number;
  analyzed_count: number;
  sampled: boolean;
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

export interface PreviewSample {
  title: string;
  year: number;
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
  estimated_cost_usd: number;
  budget_spent: number;
  budget_limit: number;
  over_limit: boolean;
  max_papers: number;
}

export interface RunResponse {
  queued: number[];
  blocked: { subfield_id: number; reason: string }[];
}
