import { useEffect } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Stats } from "../api";

// I10: source는 openalex/kci 단일 소스뿐 아니라 "both"(양쪽 모두에서 발견됨)도 가진다.
const SOURCE_LABEL: Record<string, string> = {
  openalex: "국제지(OpenAlex)만",
  kci: "KCI만",
  both: "중복(KCI+국제지)",
};

export default function StatsPanel({ stats }: { stats: Stats | Record<string, never> }) {
  // ResponsiveContainer는 마운트 시점 DOM 크기만 측정한다. 인쇄 시 @page margin으로
  // 레이아웃 폭이 바뀌므로 beforeprint/afterprint에서 resize를 발생시켜 재측정을 유도한다.
  useEffect(() => {
    const triggerResize = () => window.dispatchEvent(new Event("resize"));
    window.addEventListener("beforeprint", triggerResize);
    window.addEventListener("afterprint", triggerResize);
    return () => {
      window.removeEventListener("beforeprint", triggerResize);
      window.removeEventListener("afterprint", triggerResize);
    };
  }, []);

  if (!stats.searched_count) return null;

  const byYear = Object.entries(stats.by_year)
    .map(([year, count]) => ({ year, count }))
    .sort((a, b) => a.year.localeCompare(b.year));

  const missing = [
    { label: "abstract 없음", count: stats.no_abstract_count },
    { label: "국가 정보 없음", count: stats.no_country_count },
    { label: "출판연도 없음", count: stats.no_year_count },
    { label: "저널 정보 없음", count: stats.no_journal_count },
  ].filter((m) => m.count > 0);

  return (
    <section className="space-y-10">
      <h2 className="text-xl font-bold tracking-tight text-accent">기본 통계</h2>

      {/* 면(--surface)을 쓰는 조건은 하나다: "병렬 비교되는 동급 덩어리"일 때만.
          이 네 칸이 그 경우다. 카드를 gap으로 흩어 놓지 않고 1px 괘선 틈으로 붙여
          하나의 격자로 만든다 — 그래야 네 값이 서로 비교되는 것으로 읽힌다. */}
      <div className="avoid-break grid grid-cols-2 gap-px border border-border bg-border sm:grid-cols-4">
        <Tile label="검색 논문" value={stats.searched_count} />
        <Tile label="분석 대상" value={stats.analyzed_count} />
        <Tile label="국제공동연구" value={`${(stats.intl_collab_ratio * 100).toFixed(1)}%`} />
        <Tile
          label="피인용 중앙값"
          value={stats.citations.median}
          caption={`P90 ${stats.citations.p90.toLocaleString()} · 총 ${stats.citations.total.toLocaleString()}`}
        />
      </div>

      {missing.length > 0 && (
        <p className="avoid-break banner text-xs">
          <span className="font-medium text-ink-light">결측치 안내</span> — 검색된 논문 중{" "}
          {missing.map((m, i) => (
            <span key={m.label}>
              {i > 0 && " · "}
              {m.label} {m.count.toLocaleString()}건
            </span>
          ))}
          . 통계 분모에서 조용히 제외하지 않고 항목별로 표시합니다.
        </p>
      )}

      {byYear.length > 1 && (
        <figure className="avoid-break">
          <figcaption className="mb-2 text-sm font-bold text-ink">
            연도별 검색 논문 수
          </figcaption>
          <div role="img" aria-label="연도별 검색 논문 수 막대그래프. 같은 데이터를 아래 표로도 제공합니다.">
            {/* 격자는 뒤로 물러나고(--hair) 축은 --rule-strong, 축 라벨은 --ink-3(11px 하한).
                막대는 데이터 계열 1번 슬롯. 계열이 하나뿐이라 범례는 두지 않는다 —
                제목("연도별 검색 논문 수")이 이미 계열을 말한다. */}
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={byYear} margin={{ left: -20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" vertical={false} />
                <XAxis
                  dataKey="year"
                  tick={{ fontSize: 11, fill: "#71717a" }}
                  axisLine={{ stroke: "#a1a1aa" }}
                  tickLine={false}
                />
                <YAxis
                  allowDecimals={false}
                  tick={{ fontSize: 11, fill: "#71717a" }}
                  axisLine={false}
                  tickLine={false}
                  width={36}
                />
                <Tooltip
                  cursor={{ fill: "#f4f4f5" }}
                  contentStyle={{ borderRadius: 0, borderColor: "#d4d4d8", fontSize: 12 }}
                  formatter={(v) => [`${Number(v).toLocaleString()}건`, "검색 논문"]}
                />
                {/* 막대는 데이터 끝만 둥글게, 기준선 쪽은 각지게 둔다 */}
                <Bar dataKey="count" fill="#2a78d6" radius={[4, 4, 0, 0]} maxBarSize={40} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          {/* 차트의 텍스트 대안. 스크린리더가 읽을 수 있고, 인쇄 시에도 그대로 보여 유용하다. */}
          <table className="mt-3 w-full max-w-xs border-collapse text-xs">
            <caption className="sr-only">연도별 검색 논문 수 (표)</caption>
            <thead className="tbl-head">
              <tr className="border-b border-border-light">
                <th>연도</th>
                <th className="n">검색 논문 수</th>
              </tr>
            </thead>
            <tbody>
              {byYear.map((d) => (
                <tr key={d.year} className="border-b border-border-light">
                  <td className="py-1 text-ink-light">{d.year}</td>
                  <td className="py-1 text-right tabular-nums text-muted">
                    {d.count.toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </figure>
      )}

      <div className="grid gap-8 sm:grid-cols-2">
        <RankBars title="성과 유형별 분포" rows={objectToRows(stats.by_achievement_type)} />
        <RankBars
          title="데이터 출처"
          rows={objectToRows(stats.by_source).map(([k, v]) => [SOURCE_LABEL[k] ?? k, v])}
        />
      </div>

      <div className="grid gap-8 sm:grid-cols-2">
        <RankTable title="상위 기관" rows={stats.top_institutions} />
        <RankTable title="상위 저널" rows={stats.top_journals} />
        <RankTable title="상위 저자" rows={stats.top_authors} />
        <RankTable title="상위 협력국" rows={stats.top_partner_countries} />
      </div>

      {stats.top_cited.length > 0 && (
        <div className="avoid-break table-scroll">
          <h3 className="mb-2 text-sm font-bold text-ink">최다 피인용 논문</h3>
          <table className="w-full border-collapse text-sm">
            <thead className="tbl-head">
              <tr className="border-b border-border">
                <th>제목</th>
                <th>연도</th>
                <th>저널</th>
                <th className="n">피인용</th>
              </tr>
            </thead>
            <tbody>
              {stats.top_cited.map((p, i) => (
                <tr key={`${p.title}-${i}`} className="border-b border-border-light align-top">
                  <td className="max-w-xs py-2 pr-3">
                    {p.doi ? (
                      <a
                        href={`https://doi.org/${p.doi}`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-ink underline decoration-border underline-offset-2 hover:decoration-accent"
                      >
                        {p.title}
                      </a>
                    ) : (
                      p.title
                    )}
                  </td>
                  <td className="whitespace-nowrap py-2 pr-3 font-mono text-xs text-muted">
                    {p.year ?? <span className="text-muted">—</span>}
                  </td>
                  <td className="py-2 pr-3 text-xs text-muted">{p.journal ?? <span className="text-muted">—</span>}</td>
                  <td className="whitespace-nowrap py-2 text-right text-xs tabular-nums text-ink-light">
                    {p.citations.toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function objectToRows(obj: Record<string, number>): [string, number][] {
  return Object.entries(obj).sort((a, b) => b[1] - a[1]);
}

function Tile({ label, value, caption }: { label: string; value: string | number; caption?: string }) {
  return (
    <div className="bg-surface p-4">
      <p className="text-eyebrow font-bold uppercase tracking-[0.09em] text-muted">{label}</p>
      <p className="mt-2 text-2xl font-extrabold tabular-nums tracking-tight text-ink">
        {typeof value === "number" ? value.toLocaleString() : value}
      </p>
      {caption && <p className="mt-1 text-xs text-muted">{caption}</p>}
    </div>
  );
}

function RankBars({ title, rows }: { title: string; rows: [string, number][] }) {
  if (!rows.length) return null;
  const max = Math.max(...rows.map(([, v]) => v));
  return (
    <div className="avoid-break">
      <h3 className="mb-2 text-sm font-bold text-ink">{title}</h3>
      <ul className="space-y-1.5">
        {rows.slice(0, 8).map(([name, count]) => (
          <li key={name} className="flex items-center gap-2 text-sm">
            <span className="w-28 shrink-0 truncate text-ink-light" title={name}>
              {name}
            </span>
            <span className="h-2.5 flex-1 bg-border-light">
              <span
                className="block h-full bg-d1"
                style={{ width: `${max > 0 ? (count / max) * 100 : 0}%` }}
              />
            </span>
            <span className="w-10 shrink-0 text-right text-xs tabular-nums text-muted">
              {count.toLocaleString()}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RankTable({ title, rows }: { title: string; rows: [string, number][] }) {
  if (!rows.length) return null;
  return (
    <div className="avoid-break table-scroll">
      <h3 className="mb-2 text-sm font-bold text-ink">{title}</h3>
      <table className="w-full border-collapse text-sm">
        <tbody>
          {rows.slice(0, 10).map(([name, count]) => (
            <tr key={name} className="border-b border-border-light">
              <td className="py-1.5 pr-3 text-ink-light">{name}</td>
              <td className="py-1.5 text-right text-xs tabular-nums text-muted">
                {count.toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
