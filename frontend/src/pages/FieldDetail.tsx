import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { get, type Field, type FieldSummary, type YearRow } from "../api";
import TopBar from "../components/TopBar";
import Footer from "../components/Footer";
import StatusBadge from "../components/StatusBadge";
import CoverageBar from "../components/CoverageBar";

export default function FieldDetail() {
  const { fieldId } = useParams();
  const [field, setField] = useState<Field | null>(null);
  const [years, setYears] = useState<YearRow[]>([]);
  const [year, setYear] = useState<number | null>(null);
  const [summary, setSummary] = useState<FieldSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<Field[]>("/fields")
      .then((all) => {
        const found = all.find((f) => f.id === Number(fieldId));
        if (!found) {
          setError("요청하신 분야를 찾을 수 없습니다.");
          return;
        }
        setField(found);
      })
      .catch((e) => setError(e.message));
    get<YearRow[]>(`/fields/${fieldId}/years`)
      .then((rows) => {
        setYears(rows);
        setYear(rows[0]?.year ?? null);
      })
      .catch((e) => setError(e.message));
  }, [fieldId]);

  useEffect(() => {
    if (year == null) return;
    // 연도를 바꾸면 이전 연도 표를 즉시 지운다 — 남겨두면 위 제목은 새 연도인데 아래
    // 표는 옛 연도인 상태가 응답이 올 때까지 보인다. stale 플래그는 탭을 빠르게 여러 번
    // 눌렀을 때 응답이 뒤바뀌어 도착해 엉뚱한 연도가 남는 것을 막는다.
    setSummary(null);
    let stale = false;
    get<FieldSummary>(`/fields/${fieldId}/summary?year=${year}`)
      .then((s) => !stale && setSummary(s))
      .catch((e) => !stale && setError(e.message));
    return () => {
      stale = true;
    };
  }, [fieldId, year]);

  if (error) {
    return (
      <div className="min-h-screen">
        <TopBar />
        <main className="mx-auto max-w-5xl px-6 py-14">
          <p className="text-sm text-danger">{error}</p>
          <Link to="/" className="mt-4 inline-block text-sm text-muted hover:text-ink">
            ← 분야 목록으로 돌아가기
          </Link>
        </main>
        <Footer />
      </div>
    );
  }
  if (!field) return <p className="p-8 text-sm text-muted">불러오는 중…</p>;

  return (
    <div className="min-h-screen">
      <TopBar />
      <main className="mx-auto max-w-5xl px-6 py-14">
        <Link to="/" className="text-sm text-muted hover:text-ink">
          ← 분야 목록
        </Link>
        <h1 className="mt-2 font-display text-3xl font-bold tracking-tight text-ink">
          {field.name}
        </h1>

        {years.length === 0 ? (
          <p className="mt-8 text-sm text-muted">아직 분석된 결과가 없습니다.</p>
        ) : (
          <>
            {/* role="tab"을 제대로 쓰려면 aria-controls·tabpanel·화살표 키 이동까지 필요한데,
                여기 실제로 필요한 건 "연도 필터 토글"이다 — 반쪽짜리 tablist보다 aria-pressed가
                동작과 맞고 추가로 구현할 것도 없다. */}
            <div className="mt-8 flex flex-wrap gap-2" aria-label="연도 선택">
              {years.map((y) => (
                <button
                  key={y.year}
                  type="button"
                  aria-pressed={y.year === year}
                  onClick={() => setYear(y.year)}
                  className={`border px-3 py-1.5 font-mono text-sm transition-colors ${
                    y.year === year
                      ? "border-ink bg-ink text-paper"
                      : "border-border text-ink-light hover:border-accent"
                  }`}
                >
                  {y.year}
                  <span className="ml-1.5 opacity-70">
                    ({y.done_count}/{y.subfield_count})
                  </span>
                </button>
              ))}
            </div>

            {summary && (
              <>
                <p className="mt-4 text-sm text-ink-light">
                  {summary.year}년 검색 {summary.total_searched.toLocaleString()}건 / 분석 대상{" "}
                  {summary.total_analyzed.toLocaleString()}건
                </p>

                <div className="mt-6 overflow-x-auto border-t border-border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border text-left text-xs text-muted">
                        <th className="py-2 font-medium">세부기술</th>
                        <th className="py-2 font-medium">상태</th>
                        <th className="hidden py-2 font-medium sm:table-cell">모집단</th>
                        <th className="py-2 pr-2 text-right font-medium">검색/분석</th>
                      </tr>
                    </thead>
                    <tbody>
                      {summary.subfields.map((s) => (
                        <tr key={s.subfield_id} className="border-b border-border-light">
                          <td className="py-3 pr-3 font-medium text-ink">
                            {s.analysis_id ? (
                              <Link
                                to={`/subfields/${s.subfield_id}/${year}`}
                                className="hover:text-accent hover:underline"
                              >
                                {s.subfield_name}
                              </Link>
                            ) : (
                              s.subfield_name
                            )}
                          </td>
                          <td className="py-3 pr-3">
                            <StatusBadge status={s.status} label={s.status_label} />
                          </td>
                          <td className="hidden w-40 py-3 pr-3 sm:table-cell">
                            {s.searched_count > 0 && (
                              <CoverageBar searched={s.searched_count} analyzed={s.analyzed_count} size="sm" />
                            )}
                          </td>
                          <td className="py-3 pl-2 text-right font-mono text-xs tabular-nums text-muted">
                            {s.searched_count.toLocaleString()} / {s.analyzed_count.toLocaleString()}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )}
      </main>
      <Footer />
    </div>
  );
}
