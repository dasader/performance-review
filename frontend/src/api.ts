const BASE = import.meta.env.VITE_API_BASE ?? "/api";

export async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? "요청에 실패했습니다.");
  }
  return res.json();
}

export async function post<T>(path: string, body: unknown, adminKey?: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(adminKey ? { "X-Admin-Key": adminKey } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const body2 = await res.json().catch(() => null);
    throw new Error(body2?.detail ?? "요청에 실패했습니다.");
  }
  return res.json();
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
  stats: Stats | null;
  searched_count: number;
  analyzed_count: number;
  sampled: boolean;
  snapshot_at: string | null;
  error: string | null;
}
