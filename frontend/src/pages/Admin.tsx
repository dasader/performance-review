import { useCallback, useEffect, useState } from "react";
import {
  ACTIVE_STATUSES,
  ApiError,
  del,
  get,
  post,
  type AdminSubfield,
  type DashboardResponse,
  type DashboardRow,
  type Field,
} from "../api";
import { useAdminKey } from "../useAdminKey";
import TopBar from "../components/TopBar";
import Footer from "../components/Footer";
import StatusBadge from "../components/StatusBadge";
import SubfieldEditor from "../components/SubfieldEditor";
import RunDialog from "../components/RunDialog";
import ScheduleSection from "../components/ScheduleSection";
import RoadmapEditor from "../components/RoadmapEditor";
import FieldReportsPanel from "../components/FieldReportsPanel";

// 관리자 화면이 세로로 길어져 스크롤로만 탐색하게 됐다. 작업 단위로 묶는다.
// "분석 실행"과 "실행 상태"는 한 탭에 둔다 — 실행한 뒤 바로 상태를 보는 흐름이라
// 나누면 탭을 오가야 한다.
const TABS = [
  { id: "subfields", label: "세부기술·검색식" },
  { id: "run", label: "분석 실행·상태" },
  { id: "schedule", label: "자동 스케줄" },
  { id: "roadmap", label: "전략기술로드맵" },
  { id: "field-reports", label: "분야 보고서" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function Admin() {
  const [tab, setTab] = useState<TabId>("subfields");
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
  const [deletingId, setDeletingId] = useState<number | null>(null);

  // /admin/subfields (active 포함) 결과 — 탭이 나뉜 뒤로는 Admin이 직접 읽는다.
  // SubfieldEditor가 끌어올려 주던 값에 의존하면, 사용자가 "분석 실행" 탭으로 바로
  // 들어갔을 때 SubfieldEditor가 마운트되지 않아 실행 폼이 영영 뜨지 않는다.
  // 아래 주석은 그 시절의 설명이다:
  // (구) SubfieldEditor가 이미 불러온 것을 끌어올려
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

  const handleDeleteAnalysis = async (row: DashboardRow, cell: { analysis_id: number; year: number }) => {
    const ok = confirm(
      `'${row.subfield_name}' ${cell.year}년 분석을 삭제할까요?\n\n` +
        `지워지는 것: 보고서·통계·실행 이력\n` +
        `남는 것: 수집한 논문과 추출 결과(캐시) — 재실행 시 추출 비용 없이 다시 만들어집니다.\n\n` +
        `이 작업은 취소할 수 없습니다. 계속할까요?`,
    );
    if (!ok) return;
    setDeletingId(cell.analysis_id);
    try {
      await del(`/admin/analyses/${cell.analysis_id}`, key);
      await loadDashboard(key);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      setError(e instanceof Error ? e.message : "삭제 요청에 실패했습니다.");
    } finally {
      setDeletingId(null);
    }
  };

  const loadFields = useCallback(() => {
    setFieldsError(null);
    get<Field[]>("/fields")
      .then(setFields)
      .catch((e) => setFieldsError(e instanceof Error ? e.message : "분야 목록을 불러오지 못했습니다."));
  }, []);

  const loadSubfields = useCallback(() => {
    if (!key) return;
    get<AdminSubfield[]>("/admin/subfields", key)
      .then(setSubfields)
      .catch(() => setSubfields([]));
  }, [key]);

  useEffect(() => {
    loadFields();
    loadSubfields();
  }, [loadFields, loadSubfields]);

  if (!key) {
    return (
      <div className="min-h-screen">
        <TopBar />
        <main className="mx-auto max-w-sm px-6 py-24">
          <p className="text-eyebrow font-bold uppercase tracking-[0.09em] text-muted">관리자</p>
          <h1 className="mt-2 text-2xl font-bold tracking-tight text-ink">관리자 인증</h1>
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
              className="mt-2 input"
              required
            />
            <button
              type="submit"
              disabled={authing || !input}
              className="mt-4 btn btn-primary w-full"
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
      <main className="mx-auto max-w-page px-6 pb-10 pt-6">
        <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-eyebrow font-bold uppercase tracking-[0.09em] text-muted">관리자</p>
            <h1 className="mt-1 text-2xl font-bold tracking-tight text-ink">분석 운영</h1>
          </div>
          <div className="flex items-center gap-4">
            {data && (
              <p className="text-xs tabular-nums text-muted">
                OpenAlex 오늘 사용 ${data.budget_spent.toFixed(4)} / ${data.budget_limit.toFixed(2)}
              </p>
            )}
            <button
              type="button"
              onClick={clear}
              className="btn btn-neutral btn-sm"
            >
              로그아웃
            </button>
          </div>
        </header>

        {/* role="tablist"를 제대로 쓰려면 화살표 키 이동·aria-controls까지 필요하다.
            여기 필요한 건 화면 전환 토글이라 FieldDetail의 연도 선택과 같이 aria-pressed를 쓴다. */}
        <div className="mb-6 flex flex-wrap gap-2 border-b border-border pb-3" aria-label="관리 메뉴">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              aria-pressed={tab === t.id}
              onClick={() => setTab(t.id)}
className="btn btn-toggle btn-sm"
            >
              {t.label}
            </button>
          ))}
        </div>

        {error && <p className="mb-6 banner banner-risk">{error}</p>}

        {fields === null && !fieldsError && <p className="text-sm text-muted">분야 목록을 불러오는 중…</p>}
        {fieldsError && (
          <p className="mb-6 banner banner-risk">
            {fieldsError}{" "}
            <button type="button" onClick={loadFields} className="ml-1 underline hover:text-danger/80">
              다시 시도
            </button>
          </p>
        )}
        {tab === "subfields" && fields && (
          <SubfieldEditor
            adminKey={key}
            fields={fields}
            onChanged={() => {
              loadDashboard(key);
              loadSubfields();
              setSubfieldGen((g) => g + 1);
            }}
            onUnauthorized={onUnauthorized}
          />
        )}

        {tab === "run" && data && subfields && (
          <RunDialog
            adminKey={key}
            rows={subfields
              .filter((s) => s.active)
              .map((s) => ({ subfield_id: s.id, subfield_name: s.name }))}
            // default_year_range는 "최근 N개년"의 N(개수)이다 — 연도 범위가 아니다.
            defaultYearFrom={currentYear - (data.default_year_range - 1)}
            defaultYearTo={currentYear}
            subfieldsVersion={subfieldGen}
            onRan={() => loadDashboard(key)}
            onUnauthorized={onUnauthorized}
          />
        )}

        {tab === "schedule" && <ScheduleSection adminKey={key} onUnauthorized={onUnauthorized} />}

        {tab === "roadmap" && fields && <RoadmapEditor adminKey={key} fields={fields} />}

        {tab === "field-reports" && (
          <FieldReportsPanel adminKey={key} onUnauthorized={onUnauthorized} />
        )}

        {/* 세부기술·검색식 / 분석 실행 섹션과 같은 카드로 묶어 시각적 단위를 맞춘다. */}
        {tab === "run" && (
        <section className="mt-6 border border-border bg-surface p-5">
        <h2 className="mb-3 text-lg font-semibold text-accent">실행 상태</h2>

        {!data && !error && <p className="text-sm text-muted">불러오는 중…</p>}

        {data && data.rows.length === 0 && (
          <p className="text-sm text-muted">세부기술을 추가하면 실행 상태가 표시됩니다.</p>
        )}

        {data && data.rows.length > 0 && (
          <div className="table-scroll border-t border-border">
            <table className="w-full border-collapse text-sm">
              <thead className="tbl-head">
                <tr className="border-b border-border">
                  <th>세부기술</th>
                  <th>연도</th>
                  <th>상태</th>
                  <th className="n">검색/분석</th>
                  <th>최종수집</th>
                  <th>
                    <span>동작</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.rows.flatMap((row) =>
                  row.years.map((cell) => (
                    <tr key={cell.analysis_id} className="border-b border-border-light">
                      <td className="py-3 pr-3 font-medium text-ink">{row.subfield_name}</td>
                      <td className="py-3 pr-3 tabular-nums text-ink-light">{cell.year}</td>
                      <td className="py-3 pr-3">
                        <StatusBadge status={cell.status} label={cell.status_label} />
                        {cell.stale && (
                          <span className="ml-2 inline-flex items-center gap-1 text-xs font-medium text-warning">
                            <span aria-hidden="true">⟳</span> 갱신 필요
                          </span>
                        )}
                        {cell.error && <p className="mt-1 max-w-xs text-xs text-danger">{cell.error}</p>}
                      </td>
                      <td className="py-3 pr-3 text-right text-xs tabular-nums text-muted">
                        {cell.searched_count.toLocaleString()} / {cell.analyzed_count.toLocaleString()}
                      </td>
                      <td className="py-3 pr-3 text-xs text-faint">
                        {cell.snapshot_at ? new Date(cell.snapshot_at).toLocaleDateString("ko-KR") : "—"}
                      </td>
                      <td className="py-3 text-right whitespace-nowrap">
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
                            className="mr-2 btn btn-neutral btn-sm"
                          >
                            {retryingId === cell.analysis_id ? "요청 중…" : "재실행"}
                          </button>
                        )}
                        {/* 진행 중(ACTIVE_STATUSES)에는 숨긴다 — batch가 이미 제출됐을 수 있어
                            중간에 지우면 고아 상태가 된다(백엔드도 409로 같은 판단을 한다). */}
                        {!ACTIVE_STATUSES.has(cell.status) && (
                          <button
                            type="button"
                            disabled={deletingId === cell.analysis_id}
                            onClick={() => handleDeleteAnalysis(row, cell)}
                            className="btn btn-danger-quiet btn-sm"
                          >
                            {deletingId === cell.analysis_id ? "삭제 중…" : "삭제"}
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
        </section>
        )}
      </main>
      <Footer />
    </div>
  );
}
