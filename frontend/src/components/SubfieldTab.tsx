import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  del,
  get,
  post,
  queueAll,
  type DashboardResponse,
  type DashboardRow,
  type QueueResponse,
} from "../api";
import { COUNTRY_NAMES, sortCountries } from "../lib/countries";
import { ACTIVE_STATUSES, STATUS_LABEL } from "../lib/status";
import { usePolling } from "../lib/hooks";
import { estimateCost } from "../lib/cost";
import {
  cellKey,
  hasPendingWork,
  headerState,
  rowCells,
  toQueuePayload,
  toggleAll,
} from "../lib/selection";
import EstimatePanel from "./EstimatePanel";
import StatusBadge from "./StatusBadge";
import YearInput from "./YearInput";

// 관리자 "세부기술" 탭 — 기술 × 국가 현황과 생성이 한 화면에 있다.
//
// 예전에는 "분석 실행·상태"(연도 축, 국가 미표시) · "국가 현황"(국가 축, 일괄 생성만) ·
// "국가 비교"(현황 없이 임의 조합 생성) 세 탭에 흩어져 있었다. 같은 대상을 보는 곳과
// 만드는 곳이 달라, 무엇이 어디까지 됐는지도 이 버튼이 무엇을 대상으로 하는지도
// 알기 어려웠다. 셀을 체크해 고르면 대상이 눈으로 보인다.
export default function SubfieldTab({
  adminKey,
  onUnauthorized,
  subfieldsVersion,
  onDashboard,
}: {
  adminKey: string;
  onUnauthorized: () => void;
  // 검색식이 바뀌면 열려 있던 정밀 견적을 폐기하기 위한 세대 값(Admin.tsx가 센다).
  subfieldsVersion: number;
  // Admin.tsx 헤더의 "오늘 사용" 예산 표시용. 이 탭이 이미 /admin/dashboard를
  // 불러오므로 Admin.tsx가 같은 데이터를 다시 조회하는 대신 이 콜백으로 받는다 —
  // 안 하면 이 탭에서 생성·삭제해도 헤더 숫자가 세션 내내 갱신되지 않는다.
  onDashboard?: (data: DashboardResponse) => void;
}) {
  const [year, setYear] = useState(new Date().getFullYear());
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [scheduleCountries, setScheduleCountries] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QueueResponse | null>(null);
  // 정밀 견적은 클릭 시점의 대상을 붙잡아 둔다. 살아 있는 선택 상태(onlyAnalysis)로
  // 조건부 렌더링하면 패널이 열린 채 체크를 건드릴 때마다 견적 패널이 언마운트→재마운트되고,
  // 그때마다 마운트 이펙트가 다시 돌아 /admin/preview를 또 부른다 — 이 호출은 OpenAlex 과금이다.
  const [estimateTarget, setEstimateTarget] = useState<{
    subfieldId: number;
    country: string;
  } | null>(null);

  const load = useCallback(() => {
    get<DashboardResponse>("/admin/dashboard", adminKey)
      .then((d) => {
        setData(d);
        onDashboard?.(d);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) return onUnauthorized();
        setError(e instanceof Error ? e.message : "현황을 불러오지 못했습니다.");
      });
    get<{ countries: string }>("/admin/schedule", adminKey)
      .then((s) => setScheduleCountries(s.countries.split(",").filter(Boolean)))
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) return onUnauthorized();
        setScheduleCountries([]);
      });
  }, [adminKey, onUnauthorized, onDashboard]);

  useEffect(load, [load]);

  // 연도를 바꾸면 선택·정밀 견적·이전 연도의 큐잉 결과 배너를 모두 비운다 — 다른
  // 연도 대상이 선택에 남으면 잘못 큐잉되고, 결과 배너를 그대로 두면 "2026년 N건
  // 큐잉됨"이 2025년으로 넘어간 뒤에도 남아 어느 연도 얘기인지 헷갈린다.
  useEffect(() => {
    setSelected(new Set());
    setEstimateTarget(null);
    setResult(null);
  }, [year]);

  const rows = data?.rows ?? [];

  // 열은 설정된 국가 + 실제로 분석이 있는 국가의 합집합이다. 설정에 없는 국가의
  // 기존 분석이 화면에서 사라지면 "안 돌렸다"와 구별되지 않는다.
  const countries = useMemo(() => {
    const present = new Set(scheduleCountries);
    for (const row of rows) {
      for (const cell of row.years) if (cell.year === year) present.add(cell.country);
    }
    return sortCountries([...present]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, scheduleCountries, year]);

  // 비교는 설정된 국가 전체를 한 보고서로 만든다. 2개 미만이면 만들 수 없다.
  const comparisonKey = useMemo(
    () => [...scheduleCountries].sort().join(","),
    [scheduleCountries],
  );
  const showComparison = scheduleCountries.length >= 2;

  const cellOf = (row: DashboardRow, country: string) =>
    row.years.find((c) => c.year === year && c.country === country);

  // 상대국 분석이 하나라도 없으면 비교를 만들 수 없다 — 선택 자체를 막는다.
  const comparisonBlocked = (row: DashboardRow) =>
    !scheduleCountries.every((c) => cellOf(row, c)?.status === "done");

  // "in_multi"는 이 1:1 조합이 이미 다국 비교 안의 대조로 들어 있다는 뜻이지
  // 진행 상태가 아니다 — 그 값을 다시 큐잉하면 있는 것을 중복 생성한다.
  const comparisonStatusOf = (row: DashboardRow) =>
    row.comparisons[String(year)]?.[comparisonKey];

  // 비활성 세부기술은 행을 보여주되(운영자가 존재를 알아야 함) 선택 후보에서는
  // 뺀다 — 예전 /admin/subfields?active=true 필터가 하던 유일한 가드였고, 대시보드는
  // active를 걸러 주지 않으므로(runner.enqueue도 마찬가지) 프론트가 직접 걸러야 한다.
  const selectableOf = (row: DashboardRow) =>
    row.active
      ? rowCells(
          row.subfield_id,
          countries,
          showComparison && !comparisonBlocked(row) && comparisonStatusOf(row) !== "in_multi",
        )
      : [];

  const allCandidates = useMemo(
    () => rows.flatMap((row) => selectableOf(row).map(cellKey)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, countries, showComparison, scheduleCountries, year],
  );

  // 진행 중인 것이 있으면 5초마다 다시 읽는다. 선택은 건드리지 않는다.
  // 분석뿐 아니라 비교의 pending도 봐야 한다 — 비교만 큐잉했을 때 폴링이 안 걸리면
  // 잡 루프가 30초마다 하나씩 처리하는 동안 화면이 얼어붙어 운영자가 재큐잉한다.
  const hasActive = hasPendingWork(rows, year);
  usePolling(hasActive, load);

  // 갱신으로 사라진 대상은 선택에서 조용히 뺀다.
  useEffect(() => {
    setSelected((prev) => {
      const valid = new Set(allCandidates);
      const next = new Set([...prev].filter((k) => valid.has(k)));
      return next.size === prev.size ? prev : next;
    });
  }, [allCandidates]);

  const papersByCell = useMemo(() => {
    const map: Record<string, number> = {};
    for (const row of rows) {
      for (const c of row.years) {
        if (c.year === year && c.searched_count > 0) {
          map[cellKey({ kind: "analysis", subfieldId: row.subfield_id, country: c.country })] =
            c.searched_count;
        }
      }
    }
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, year]);

  const payload = toQueuePayload(selected, { year, countries: scheduleCountries, force });
  const cost = estimateCost(payload, papersByCell);
  const total = payload.analyses.length + payload.comparisons.length;
  // 견적은 한 대상에 대한 것이고, 여럿을 고르면 /admin/preview 호출이 그만큼 과금된다 —
  // 분석 셀 하나만 골랐을 때만 버튼을 낸다.
  const onlyAnalysis =
    payload.analyses.length === 1 && payload.comparisons.length === 0
      ? payload.analyses[0]
      : null;

  const toggle = (key: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const runQueue = async () => {
    if (total === 0) return;
    if (
      !confirm(
        `${year}년 세부기술 분석 ${payload.analyses.length}건, ` +
          `국가 비교 ${payload.comparisons.length}건을 생성합니다.\n` +
          "LLM 호출 비용이 발생합니다. 계속할까요?",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setResult(await queueAll(payload, adminKey));
      setSelected(new Set());
      load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      setError(e instanceof Error ? e.message : "생성 요청에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-6 border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-accent">세부기술 분석·국가비교 현황</h2>
        <YearInput year={year} onChange={setYear} />
      </div>

      <p className="mt-2 text-xs text-muted">
        <strong className="text-ink">국가 칸</strong>은 그 나라의 세부기술 분석 보고서,{" "}
        <strong className="text-ink">비교 칸</strong>은 국가 간 비교 보고서입니다. 만들 것을
        체크해서 고르고 위에서 한 번에 생성합니다. 열 머리글은 그 국가 전체, 행 체크는 그
        기술 전체를 고릅니다.
        <strong className="text-ink"> —</strong>는 상대국 분석이 없어 지금은 만들 수 없는 칸입니다.
      </p>

      {/* 선택 요약 + 실행. 대상 건수를 눈으로 확인한 뒤 누르게 한다. */}
      <div className="mt-4 flex flex-wrap items-center gap-3 border border-border-light bg-paper p-3">
        <span className="text-sm text-ink">
          분석 {payload.analyses.length}건 · 비교 {payload.comparisons.length}건 선택됨
        </span>
        {cost.reportUsd > 0 && (
          <span className="text-xs text-muted">보고서 예상 ${cost.reportUsd.toFixed(2)}</span>
        )}
        {payload.analyses.length > 0 && (
          <span className="text-xs text-muted">
            {cost.analysisPapers === null
              ? "분석 비용은 검색 결과에 따라 달라집니다"
              : `분석은 과거 실적 ${cost.analysisPapers.toLocaleString()}편 기준`}
          </span>
        )}
        <label className="flex items-center gap-2 text-sm text-ink-light">
          <input type="checkbox" checked={force} onChange={() => setForce((v) => !v)} />
          이미 완료된 것도 다시 생성
        </label>
        <button
          type="button"
          onClick={runQueue}
          disabled={busy || total === 0}
          className="btn btn-primary btn-sm"
        >
          {busy
            ? "요청 중…"
            : `분석 ${payload.analyses.length} · 비교 ${payload.comparisons.length}건 생성`}
        </button>
        {onlyAnalysis && (
          <button
            type="button"
            onClick={() =>
              setEstimateTarget({ subfieldId: onlyAnalysis.subfield_id, country: onlyAnalysis.country })
            }
            className="btn btn-neutral btn-sm"
          >
            정밀 견적
          </button>
        )}
        {selected.size > 0 && (
          <button
            type="button"
            onClick={() => setSelected(new Set())}
            className="btn btn-neutral btn-sm"
          >
            선택 해제
          </button>
        )}
      </div>

      {error && <p className="mt-3 text-sm text-danger">{error}</p>}

      {/* 부분 실패를 사유와 함께 보여준다 — 조용히 건너뛰지 않는 것이 이 API의 요점이다. */}
      {result && (
        <div className="mt-3 border border-border-light bg-paper p-3 text-sm">
          <p className="text-ink">
            분석 {result.queued.analyses}건 · 비교 {result.queued.comparisons}건 큐잉됨
          </p>
          {result.skipped.length > 0 && (
            <ul className="mt-2 space-y-1">
              {result.skipped.map((s, i) => (
                <li key={i} className="text-xs text-muted">
                  {rows.find((r) => r.subfield_id === s.subfield_id)?.subfield_name ?? s.subfield_id}
                  {s.country ? ` · ${COUNTRY_NAMES[s.country] ?? s.country}` : ""} — {s.reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {estimateTarget && (
        <div className="mt-4">
          {/* key로 대상 변경 시 강제 리마운트한다 — 조회는 마운트 이펙트([])라 대상이
              바뀌어도 재실행되지 않는다. key가 없으면 A를 열어 둔 채 선택을 B로 바꿔
              다시 눌러도 패널이 여전히 A의 숫자를 보여준다. */}
          <EstimatePanel
            key={`${estimateTarget.subfieldId}:${estimateTarget.country}`}
            adminKey={adminKey}
            subfieldId={estimateTarget.subfieldId}
            subfieldName={
              rows.find((r) => r.subfield_id === estimateTarget.subfieldId)?.subfield_name ??
              `#${estimateTarget.subfieldId}`
            }
            country={estimateTarget.country}
            year={year}
            subfieldsVersion={subfieldsVersion}
            onUnauthorized={onUnauthorized}
          />
          <button
            type="button"
            onClick={() => setEstimateTarget(null)}
            className="mt-2 btn btn-neutral btn-sm"
          >
            견적 닫기
          </button>
        </div>
      )}

      {data && (
        <div className="mt-6 table-scroll border-t border-border">
          <table className="w-full border-collapse text-sm">
            <thead className="tbl-head">
              <tr className="border-b border-border">
                <th>세부기술</th>
                {countries.map((c) => {
                  const candidates = rows
                    .map((row) => cellKey({ kind: "analysis", subfieldId: row.subfield_id, country: c }))
                    .filter((k) => allCandidates.includes(k));
                  const state = headerState(selected, candidates);
                  return (
                    <th key={c}>
                      <label className="flex items-center justify-center gap-1">
                        <input
                          type="checkbox"
                          checked={state === "all"}
                          ref={(el) => {
                            if (el) el.indeterminate = state === "some";
                          }}
                          onChange={() =>
                            setSelected((prev) => toggleAll(prev, candidates, state !== "all"))
                          }
                        />
                        {COUNTRY_NAMES[c] ?? c}
                      </label>
                    </th>
                  );
                })}
                {showComparison && (() => {
                  // 국가 열과 같은 트리스테이트 헤더 체크박스 — 없으면 비교를 55개
                  // 세부기술마다 하나씩 55번 눌러야 한다(예전 "1:1/다국 비교 일괄
                  // 생성" 버튼이 하던 일).
                  const candidates = rows
                    .map((row) => cellKey({ kind: "comparison", subfieldId: row.subfield_id }))
                    .filter((k) => allCandidates.includes(k));
                  const state = headerState(selected, candidates);
                  return (
                    <th>
                      <label className="flex items-center justify-center gap-1">
                        <input
                          type="checkbox"
                          checked={state === "all"}
                          ref={(el) => {
                            if (el) el.indeterminate = state === "some";
                          }}
                          onChange={() =>
                            setSelected((prev) => toggleAll(prev, candidates, state !== "all"))
                          }
                        />
                        국가비교
                      </label>
                    </th>
                  );
                })()}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const candidates = selectableOf(row).map(cellKey);
                const rowState = headerState(selected, candidates);
                const open = expanded.has(row.subfield_id);
                return (
                  <ExpandableRow
                    key={row.subfield_id}
                    row={row}
                    countries={countries}
                    open={open}
                    rowState={rowState}
                    selected={selected}
                    showComparison={showComparison}
                    comparisonStatus={comparisonStatusOf(row)}
                    comparisonBlocked={comparisonBlocked(row)}
                    onToggleRow={() =>
                      setSelected((prev) => toggleAll(prev, candidates, rowState !== "all"))
                    }
                    onToggleCell={toggle}
                    onToggleExpand={() =>
                      setExpanded((prev) => {
                        const next = new Set(prev);
                        if (next.has(row.subfield_id)) next.delete(row.subfield_id);
                        else next.add(row.subfield_id);
                        return next;
                      })
                    }
                    cellOf={cellOf}
                    adminKey={adminKey}
                    onUnauthorized={onUnauthorized}
                    onError={setError}
                    onReload={load}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// 행 하나 + 펼쳤을 때의 연도 이력. 개별 분석 동작(재실행·삭제)은 여기 둔다 —
// 표의 셀은 "선택해서 만드는 것"이라는 한 가지 뜻만 갖게 한다.
function ExpandableRow({
  row, countries, open, rowState, selected, showComparison,
  comparisonStatus, comparisonBlocked, onToggleRow, onToggleCell, onToggleExpand, cellOf,
  adminKey, onUnauthorized, onError, onReload,
}: {
  row: DashboardRow;
  countries: string[];
  open: boolean;
  rowState: "none" | "some" | "all";
  selected: Set<string>;
  showComparison: boolean;
  comparisonStatus: string | undefined;
  comparisonBlocked: boolean;
  onToggleRow: () => void;
  onToggleCell: (key: string) => void;
  onToggleExpand: () => void;
  cellOf: (
    row: DashboardRow,
    country: string,
  ) => { status: string; status_label: string; stale: boolean; error: string | null } | undefined;
  adminKey: string;
  onUnauthorized: () => void;
  onError: (message: string) => void;
  onReload: () => void;
}) {
  // 이 연도를 포함한 전체 연도 이력 — 재실행/삭제 대상이 되는 유일한 곳이라
  // 현재 연도를 빼면 실패한 당해 연도 분석에 손댈 방법이 없어진다.
  const history = [...row.years].sort((a, b) => b.year - a.year);
  const [retryingId, setRetryingId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const handleRetry = async (analysisId: number) => {
    setRetryingId(analysisId);
    try {
      await post(`/admin/analyses/${analysisId}/retry`, {}, adminKey);
      onReload();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      onError(e instanceof Error ? e.message : "재실행 요청에 실패했습니다.");
    } finally {
      setRetryingId(null);
    }
  };

  const handleDelete = async (analysisId: number, analysisYear: number) => {
    const ok = confirm(
      `'${row.subfield_name}' ${analysisYear}년 분석을 삭제할까요?\n\n` +
        `지워지는 것: 보고서·통계·실행 이력\n` +
        `남는 것: 수집한 논문과 추출 결과(캐시) — 재실행 시 추출 비용 없이 다시 만들어집니다.\n\n` +
        `이 작업은 취소할 수 없습니다. 계속할까요?`,
    );
    if (!ok) return;
    setDeletingId(analysisId);
    try {
      await del(`/admin/analyses/${analysisId}`, adminKey);
      onReload();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      onError(e instanceof Error ? e.message : "삭제 요청에 실패했습니다.");
    } finally {
      setDeletingId(null);
    }
  };
  const active = row.active;
  return (
    <>
      <tr className={`border-b border-border-light ${active ? "" : "opacity-60"}`}>
        <td className="py-3 pr-3">
          {active ? (
            <label className="flex items-center gap-2 font-medium text-ink">
              <input
                type="checkbox"
                checked={rowState === "all"}
                ref={(el) => {
                  if (el) el.indeterminate = rowState === "some";
                }}
                onChange={onToggleRow}
              />
              <button type="button" onClick={onToggleExpand} className="text-left">
                <span aria-hidden="true">{open ? "▾" : "▸"}</span> {row.subfield_name}
              </button>
            </label>
          ) : (
            // 비활성 세부기술 — 체크박스를 아예 안 그린다("—" 막힌 비교 칸과 같은 패턴).
            // 존재는 보여주되(운영자가 알아야 함) 고를 수는 없다.
            <button
              type="button"
              onClick={onToggleExpand}
              className="flex items-center gap-2 text-left font-medium text-muted"
              title="비활성화된 세부기술 — 선택할 수 없습니다"
            >
              <span aria-hidden="true">{open ? "▾" : "▸"}</span> {row.subfield_name}
              <span className="text-xs text-faint">(비활성)</span>
            </button>
          )}
        </td>
        {countries.map((c) => {
          const cell = cellOf(row, c);
          const key = cellKey({ kind: "analysis", subfieldId: row.subfield_id, country: c });
          return (
            <td key={c} className="py-3 pr-3 text-center">
              {active ? (
                <label className="inline-flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={selected.has(key)}
                    onChange={() => onToggleCell(key)}
                  />
                  {cell ? (
                    <StatusBadge status={cell.status} label={STATUS_LABEL[cell.status] ?? cell.status} />
                  ) : (
                    <span className="text-xs text-muted">미생성</span>
                  )}
                </label>
              ) : cell ? (
                <StatusBadge status={cell.status} label={STATUS_LABEL[cell.status] ?? cell.status} />
              ) : (
                <span className="text-xs text-faint">—</span>
              )}
              {cell?.stale && <p className="text-xs text-warning">갱신 필요</p>}
            </td>
          );
        })}
        {showComparison && (
          <td className="py-3 text-center">
            {!active || (comparisonBlocked && !comparisonStatus) ? (
              <span
                className="text-faint"
                title={active ? "상대국 분석이 없어 지금은 만들 수 없습니다" : "비활성화된 세부기술 — 선택할 수 없습니다"}
              >
                —
              </span>
            ) : comparisonStatus === "in_multi" ? (
              // 실행 상태가 아니라 "이미 다국 비교 안에 들어 있다"는 사실이라 점 배지를
              // 쓰지 않는다 — 여기에 체크박스를 주면 있는 것을 중복 생성하게 된다.
              <span
                className="text-xs text-muted"
                title="다국 비교 안에 1:1 대조로 들어 있습니다 — 따로 만들 필요가 없습니다."
              >
                다국에 포함
              </span>
            ) : (
              <label className="inline-flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={selected.has(cellKey({ kind: "comparison", subfieldId: row.subfield_id }))}
                  disabled={comparisonBlocked}
                  onChange={() =>
                    onToggleCell(cellKey({ kind: "comparison", subfieldId: row.subfield_id }))
                  }
                />
                {comparisonStatus ? (
                  <StatusBadge
                    status={comparisonStatus}
                    label={STATUS_LABEL[comparisonStatus] ?? comparisonStatus}
                  />
                ) : (
                  <span className="text-xs text-muted">미생성</span>
                )}
              </label>
            )}
          </td>
        )}
      </tr>
      {open && (
        <tr className="border-b border-border-light bg-sunken">
          <td colSpan={countries.length + (showComparison ? 2 : 1)} className="py-3 pr-3">
            {history.length === 0 ? (
              <p className="text-xs text-muted">아직 분석이 없습니다.</p>
            ) : (
              <ul className="space-y-1">
                {history.map((c) => (
                  <li key={c.analysis_id} className="flex flex-wrap items-center gap-2 text-xs text-muted">
                    <span>
                      {c.year} · {COUNTRY_NAMES[c.country] ?? c.country} ·{" "}
                      {STATUS_LABEL[c.status] ?? c.status} · 검색 {c.searched_count.toLocaleString()} /
                      분석 {c.analyzed_count.toLocaleString()}
                      {c.stale && <span className="ml-2 text-warning">갱신 필요</span>}
                    </span>
                    {/* 재실행 버튼만 있으면 실패 사유를 몰라 재실행이 유일한 선택지가
                        되고, 그 재실행도 같은 이유로 똑같이 실패한다 — 사유를 보여준다. */}
                    {c.error && <span className="w-full text-danger">{c.error}</span>}
                    {(c.status === "failed" || c.status === "paused") && (
                      <button
                        type="button"
                        disabled={retryingId === c.analysis_id}
                        onClick={() => handleRetry(c.analysis_id)}
                        className="btn btn-neutral btn-sm"
                      >
                        {retryingId === c.analysis_id ? "요청 중…" : "재실행"}
                      </button>
                    )}
                    {/* 진행 중(ACTIVE_STATUSES)에는 숨긴다 — batch가 이미 제출됐을 수 있어
                        중간에 지우면 고아 상태가 된다(백엔드도 409로 같은 판단을 한다). */}
                    {!ACTIVE_STATUSES.has(c.status) && (
                      <button
                        type="button"
                        disabled={deletingId === c.analysis_id}
                        onClick={() => handleDelete(c.analysis_id, c.year)}
                        className="btn btn-danger-quiet btn-sm"
                      >
                        {deletingId === c.analysis_id ? "삭제 중…" : "삭제"}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
