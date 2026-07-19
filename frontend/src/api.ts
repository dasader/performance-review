const BASE = import.meta.env.VITE_API_BASE ?? "/api";

const NETWORK_ERROR_MESSAGE = "서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.";
const GENERIC_ERROR_MESSAGE = "요청에 실패했습니다.";

// fetch() 자체가 reject되는 경우(백엔드 다운, DNS 실패 등)는 브라우저가 영어 메시지를
// 던지므로 여기서 한국어 메시지로 변환한다. get/post가 공유하는 지점이라 한 번만 처리하면 된다.
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

export async function get<T>(path: string): Promise<T> {
  const res = await doFetch(path);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? GENERIC_ERROR_MESSAGE);
  }
  return parseJson<T>(res);
}

export async function post<T>(path: string, body: unknown, adminKey?: string): Promise<T> {
  const res = await doFetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(adminKey ? { "X-Admin-Key": adminKey } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const body2 = await res.json().catch(() => null);
    throw new Error(body2?.detail ?? GENERIC_ERROR_MESSAGE);
  }
  return parseJson<T>(res);
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
