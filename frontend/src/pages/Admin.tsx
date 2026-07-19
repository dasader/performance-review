import { useCallback, useEffect, useState } from "react";
import { ApiError, get, post, type AdminSubfield, type DashboardResponse, type Field } from "../api";
import { useAdminKey } from "../useAdminKey";
import TopBar from "../components/TopBar";
import Footer from "../components/Footer";
import StatusBadge from "../components/StatusBadge";
import SubfieldEditor from "../components/SubfieldEditor";
import RunDialog from "../components/RunDialog";

export default function Admin() {
  // 백엔드의 default_year_range는 "최근 N개년"의 N(정수)이다. 연도 범위가 아니라 개수.
  const currentYear = new Date().getFullYear();
  const { key, save, clear } = useAdminKey();
  const [input, setInput] = useState("");
  const [authing, setAuthing] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  const [data, setData] = useState<DashboardResponse | null>(null);
  const [fields, setFields] = useState<Field[] | null>(null);
  const [fieldsError, setFieldsError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<number | null>(null);

  // /admin/subfields (active 포함) 결과 — SubfieldEditor가 이미 불러온 것을 끌어올려
  // RunDialog의 실행 대상 목록에서 비활성 세부기술을 제외하는 데 재사용한다.
  const [subfields, setSubfields] = useState<AdminSubfield[] | null>(null);
  // SubfieldEditor에서 검색식이 바뀔 때마다 증가하는 세대 카운터. RunDialog는 이 값이
  // 바뀌면 확인했던 미리보기 숫자가 더 이상 유효하지 않다고 보고 폐기한다.
  const [subfieldGen, setSubfieldGen] = useState(0);

  const onUnauthorized = useCallback(() => {
    clear();
    setData(null);
    setError("관리자 키가 올바르지 않거나 만료되었습니다. 다시 인증해 주세요.");
  }, [clear]);

  const loadDashboard = useCallback(
    async (adminKey: string) => {
      try {
        setData(await get<DashboardResponse>("/admin/dashboard", adminKey));
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return onUnauthorized();
        setError(e instanceof Error ? e.message : "대시보드를 불러오지 못했습니다.");
      }
    },
    [onUnauthorized],
  );

  useEffect(() => {
    if (key) loadDashboard(key);
  }, [key, loadDashboard]);

  const loadFields = useCallback(() => {
    setFieldsError(null);
    get<Field[]>("/fields")
      .then(setFields)
      .catch((e) => setFieldsError(e instanceof Error ? e.message : "분야 목록을 불러오지 못했습니다."));
  }, []);

  useEffect(() => {
    loadFields();
  }, [loadFields]);

  if (!key) {
    return (
      <div className="min-h-screen">
        <TopBar />
        <main className="mx-auto max-w-sm px-6 py-24">
          <p className="font-mono text-xs uppercase tracking-widest text-accent">관리자</p>
          <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-ink">관리자 인증</h1>
          <p className="mt-2 text-sm text-ink-light">
            분석 실행·검색식 편집은 관리자 키가 있어야 접근할 수 있습니다.
          </p>

          <form
            className="mt-6"
            onSubmit={async (e) => {
              e.preventDefault();
              setAuthing(true);
              setAuthError(null);
              try {
                await post("/admin/auth", {}, input);
                save(input);
                setInput("");
              } catch {
                setAuthError("관리자 키가 올바르지 않습니다.");
              } finally {
                setAuthing(false);
              }
            }}
          >
            <label htmlFor="admin-key" className="block text-sm font-medium text-ink-light">
              관리자 키
            </label>
            <input
              id="admin-key"
              type="password"
              autoComplete="off"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              className="mt-2 w-full border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent"
              required
            />
            <button
              type="submit"
              disabled={authing || !input}
              className="mt-4 w-full border border-ink bg-ink py-2 text-sm font-medium text-paper transition-colors hover:bg-ink/90 disabled:opacity-40"
            >
              {authing ? "확인 중…" : "접속"}
            </button>
            {authError && <p className="mt-3 text-sm text-danger">{authError}</p>}
          </form>
        </main>
        <Footer />
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <TopBar />
      <main className="mx-auto max-w-6xl px-6 py-10">
        <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="font-mono text-xs uppercase tracking-widest text-accent">관리자</p>
            <h1 className="mt-1 font-display text-2xl font-bold tracking-tight text-ink">분석 운영</h1>
          </div>
          <div className="flex items-center gap-4">
            {data && (
              <p className="font-mono text-xs tabular-nums text-muted">
                OpenAlex 오늘 사용 ${data.budget_spent.toFixed(4)} / ${data.budget_limit.toFixed(2)}
              </p>
            )}
            <button
              type="button"
              onClick={clear}
              className="border border-border px-3 py-1.5 text-xs text-ink-light hover:border-accent hover:text-accent"
            >
              로그아웃
            </button>
          </div>
        </header>

        {error && <p className="mb-6 border border-danger/40 bg-danger/5 px-4 py-3 text-sm text-danger">{error}</p>}

        {fields === null && !fieldsError && <p className="text-sm text-muted">분야 목록을 불러오는 중…</p>}
        {fieldsError && (
          <p className="mb-6 border border-danger/40 bg-danger/5 px-4 py-3 text-sm text-danger">
            {fieldsError}{" "}
            <button type="button" onClick={loadFields} className="ml-1 underline hover:text-danger/80">
              다시 시도
            </button>
          </p>
        )}
        {fields && (
          <SubfieldEditor
            adminKey={key}
            fields={fields}
            onChanged={() => {
              loadDashboard(key);
              setSubfieldGen((g) => g + 1);
            }}
            onUnauthorized={onUnauthorized}
            onItemsLoaded={setSubfields}
          />
        )}

        {data && subfields && (() => {
          const defaultYearFrom = currentYear - (data.default_year_range - 1);
          return (
          <RunDialog
            adminKey={key}
            rows={subfields
              .filter((s) => s.active)
              .map((s) => ({ subfield_id: s.id, subfield_name: s.name }))}
            defaultYearFrom={defaultYearFrom}
            defaultYearTo={currentYear}
            subfieldsVersion={subfieldGen}
            onRan={() => loadDashboard(key)}
            onUnauthorized={onUnauthorized}
          />
          );
        })()}

        <h2 className="mb-3 mt-10 font-display text-lg font-semibold text-ink">실행 상태</h2>

        {!data && !error && <p className="text-sm text-muted">불러오는 중…</p>}

        {data && data.rows.length === 0 && (
          <p className="text-sm text-muted">세부기술을 추가하면 실행 상태가 표시됩니다.</p>
        )}

        {data && data.rows.length > 0 && (
          <div className="overflow-x-auto border-t border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted">
                  <th className="py-2 pr-3 font-medium">세부기술</th>
                  <th className="py-2 pr-3 font-medium">연도</th>
                  <th className="py-2 pr-3 font-medium">상태</th>
                  <th className="py-2 pr-3 text-right font-medium">검색/분석</th>
                  <th className="py-2 pr-3 font-medium">최종수집</th>
                  <th className="py-2 font-medium">
                    <span className="sr-only">동작</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.rows.flatMap((row) =>
                  row.years.map((cell) => (
                    <tr key={cell.analysis_id} className="border-b border-border-light">
                      <td className="py-3 pr-3 font-medium text-ink">{row.subfield_name}</td>
                      <td className="py-3 pr-3 font-mono tabular-nums text-ink-light">{cell.year}</td>
                      <td className="py-3 pr-3">
                        <StatusBadge status={cell.status} label={cell.status_label} />
                        {cell.stale && (
                          <span className="ml-2 inline-flex items-center gap-1 text-xs font-medium text-warning">
                            <span aria-hidden="true">⟳</span> 갱신 필요
                          </span>
                        )}
                        {cell.error && <p className="mt-1 max-w-xs text-xs text-danger">{cell.error}</p>}
                      </td>
                      <td className="py-3 pr-3 text-right font-mono text-xs tabular-nums text-muted">
                        {cell.searched_count.toLocaleString()} / {cell.analyzed_count.toLocaleString()}
                      </td>
                      <td className="py-3 pr-3 text-xs text-faint">
                        {cell.snapshot_at ? new Date(cell.snapshot_at).toLocaleDateString("ko-KR") : "—"}
                      </td>
                      <td className="py-3 text-right">
                        {(cell.status === "failed" || cell.status === "paused") && (
                          <button
                            type="button"
                            disabled={retryingId === cell.analysis_id}
                            onClick={async () => {
                              setRetryingId(cell.analysis_id);
                              try {
                                await post(`/admin/analyses/${cell.analysis_id}/retry`, {}, key);
                                await loadDashboard(key);
                              } catch (e) {
                                if (e instanceof ApiError && e.status === 401) return onUnauthorized();
                                setError(e instanceof Error ? e.message : "재실행 요청에 실패했습니다.");
                              } finally {
                                setRetryingId(null);
                              }
                            }}
                            className="border border-border px-2 py-1 text-xs text-ink-light hover:border-accent hover:text-accent disabled:opacity-40"
                          >
                            {retryingId === cell.analysis_id ? "요청 중…" : "재실행"}
                          </button>
                        )}
                      </td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
        )}
      </main>
      <Footer />
    </div>
  );
}
