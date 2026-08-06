import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  STATUS_LABEL,
  deleteRoadmap,
  get,
  getRoadmap,
  putRoadmap,
  queueAll,
  type FieldReportCell,
  type FieldReportRow,
  type FieldReportsResponse,
  type QueueResponse,
} from "../api";
import { estimateCost } from "../lib/cost";
import { formatGeneratedAt } from "../lib/format";
import { usePolling } from "../lib/hooks";
import { cellKey, headerState, toQueuePayload, toggleAll } from "../lib/selection";
import StatusBadge from "./StatusBadge";
import YearInput from "./YearInput";

// 관리자 "분야 보고서" 탭 — 분야 종합·로드맵 점검 현황과 생성, 로드맵 원문 편집이
// 한 화면에 있다. 세부기술 탭과 같은 규약(체크해서 고르고 위에서 한 번에 생성).
//
// 로드맵을 여기 둔 이유: 로드맵은 분야의 속성이고 점검 보고서의 입력이다.
// "점검이 안 돌아가네" → "로드맵이 미등록이구나"가 화면 이동 없이 이어져야 한다.
export default function FieldTab({
  adminKey,
  onUnauthorized,
}: {
  adminKey: string;
  onUnauthorized: () => void;
}) {
  const [year, setYear] = useState(new Date().getFullYear());
  const [data, setData] = useState<FieldReportsResponse | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [openField, setOpenField] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QueueResponse | null>(null);

  const load = useCallback(() => {
    get<FieldReportsResponse>(`/admin/field-reports?year=${year}`, adminKey)
      .then(setData)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) return onUnauthorized();
        setError(e instanceof Error ? e.message : "현황을 불러오지 못했습니다.");
      });
  }, [year, adminKey, onUnauthorized]);

  useEffect(load, [load]);

  // 연도를 바꾸면 선택과 결과를 비운다 — 다른 연도 대상이 남으면 잘못 큐잉된다.
  useEffect(() => {
    setSelected(new Set());
    setResult(null);
  }, [year]);

  const rows = data?.rows ?? [];

  // 로드맵 점검은 로드맵이 있어야 만들 수 있다. 종합은 세부기술 보고서가 있어야 하지만
  // 그 현황이 이 응답에 없으므로 서버의 skipped 사유에 맡긴다.
  const cellsOf = (row: FieldReportRow) => {
    const keys = [cellKey({ kind: "field_report", fieldId: row.field_id })];
    if (row.roadmap) keys.push(cellKey({ kind: "roadmap_check", fieldId: row.field_id }));
    return keys;
  };
  const allCandidates = rows.flatMap(cellsOf);

  const hasPending = rows.some(
    (r) => r.report?.status === "pending" || r.roadmap_check?.status === "pending",
  );
  usePolling(hasPending, load);

  // 갱신으로 사라진 대상은 선택에서 조용히 뺀다.
  useEffect(() => {
    setSelected((prev) => {
      const valid = new Set(allCandidates);
      const next = new Set([...prev].filter((k) => valid.has(k)));
      return next.size === prev.size ? prev : next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows]);

  const payload = toQueuePayload(selected, { year, countries: [], force: false });
  const cost = estimateCost(payload, {});
  const total = payload.field_reports.length + payload.roadmap_checks.length;

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
        `${year}년 분야 종합보고서 ${payload.field_reports.length}건, ` +
          `로드맵 점검 ${payload.roadmap_checks.length}건을 생성합니다.\n` +
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

  const columnCandidates = (kind: "field_report" | "roadmap_check") =>
    rows
      .map((row) => cellKey({ kind, fieldId: row.field_id }))
      .filter((k) => allCandidates.includes(k));

  return (
    <section className="mt-6 border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-accent">분야 종합·로드맵 점검 현황</h2>
        <YearInput year={year} onChange={setYear} />
      </div>

      <p className="mt-2 text-xs text-muted">
        <strong className="text-ink">종합보고서 칸</strong>은 그 분야 세부기술 보고서를 합성한
        보고서, <strong className="text-ink">로드맵 점검 칸</strong>은 그 보고서로 로드맵 목표를
        전수 대조한 결과입니다. 만들 것을 체크해서 고르고 위에서 한 번에 생성합니다.
        <strong className="text-ink"> 대상 아님</strong>은 로드맵이 등록되지 않아 점검을 만들 수
        없는 칸입니다 — 오른쪽 로드맵 열에서 등록할 수 있습니다.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-3 border border-border-light bg-paper p-3">
        <span className="text-sm text-ink">
          종합 {payload.field_reports.length}건 · 점검 {payload.roadmap_checks.length}건 선택됨
        </span>
        {cost.reportUsd > 0 && (
          <span className="text-xs text-muted">예상 ${cost.reportUsd.toFixed(2)}</span>
        )}
        <button
          type="button"
          onClick={runQueue}
          disabled={busy || total === 0}
          className="btn btn-primary btn-sm"
        >
          {busy
            ? "요청 중…"
            : `종합 ${payload.field_reports.length} · 점검 ${payload.roadmap_checks.length}건 생성`}
        </button>
        {selected.size > 0 && (
          <button type="button" onClick={() => setSelected(new Set())} className="btn btn-neutral btn-sm">
            선택 해제
          </button>
        )}
      </div>

      {error && <p className="mt-3 text-sm text-danger">{error}</p>}

      {/* 부분 실패를 사유와 함께 보여준다 — 조용히 건너뛰지 않는 것이 이 API의 요점이다. */}
      {result && (
        <div className="mt-3 border border-border-light bg-paper p-3 text-sm">
          <p className="text-ink">
            종합 {result.queued.field_reports}건 · 점검 {result.queued.roadmap_checks}건 큐잉됨
          </p>
          {result.skipped.length > 0 && (
            <ul className="mt-2 space-y-1">
              {result.skipped.map((s, i) => (
                <li key={i} className="text-xs text-muted">
                  {rows.find((r) => r.field_id === s.field_id)?.field_name ?? s.field_id} — {s.reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {data && (
        <div className="mt-6 table-scroll border-t border-border">
          <table className="w-full border-collapse text-sm">
            <thead className="tbl-head">
              <tr className="border-b border-border">
                <th>분야</th>
                {(["field_report", "roadmap_check"] as const).map((kind) => {
                  const candidates = columnCandidates(kind);
                  const state = headerState(selected, candidates);
                  return (
                    <th key={kind}>
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
                        {kind === "field_report" ? "종합보고서" : "로드맵 점검"}
                      </label>
                    </th>
                  );
                })}
                <th>로드맵</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <FieldRow
                  key={row.field_id}
                  row={row}
                  selected={selected}
                  open={openField === row.field_id}
                  onToggleCell={toggle}
                  onToggleOpen={() =>
                    setOpenField((cur) => (cur === row.field_id ? null : row.field_id))
                  }
                  adminKey={adminKey}
                  onUnauthorized={onUnauthorized}
                  onSaved={load}
                  onError={setError}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ReportCell({
  cell,
  cellKeyStr,
  selectable,
  selected,
  onToggle,
}: {
  cell: FieldReportCell | null;
  cellKeyStr: string;
  selectable: boolean;
  selected: Set<string>;
  onToggle: (key: string) => void;
}) {
  if (!selectable) {
    // 로드맵 미등록 — 체크박스를 아예 안 그린다(회색 처리만으로는 열 전체선택에 딸려 온다).
    return (
      <span className="text-xs text-faint" title="로드맵이 등록되지 않아 점검을 만들 수 없습니다">
        대상 아님
      </span>
    );
  }
  return (
    <>
      <label className="inline-flex items-center gap-1">
        <input type="checkbox" checked={selected.has(cellKeyStr)} onChange={() => onToggle(cellKeyStr)} />
        {cell ? (
          <StatusBadge status={cell.status} label={STATUS_LABEL[cell.status] ?? cell.status} />
        ) : (
          <span className="text-xs text-muted">미생성</span>
        )}
      </label>
      {cell?.status === "done" && cell.generated_at && (
        <p className="text-xs text-muted">{formatGeneratedAt(cell.generated_at)}</p>
      )}
      {cell?.status === "failed" && cell.error && (
        <p className="max-w-xs text-xs text-danger">{cell.error}</p>
      )}
    </>
  );
}

// 분야 한 행 + 펼쳤을 때의 로드맵 편집기. 펼치기 전에는 원문을 가져오지도 그리지도
// 않는다 — 10개 분야 × 13KB를 목록 응답에 실을 이유가 없고, 텍스트영역이 표를 밀어낸다.
function FieldRow({
  row, selected, open, onToggleCell, onToggleOpen, adminKey, onUnauthorized, onSaved, onError,
}: {
  row: FieldReportRow;
  selected: Set<string>;
  open: boolean;
  onToggleCell: (key: string) => void;
  onToggleOpen: () => void;
  adminKey: string;
  onUnauthorized: () => void;
  onSaved: () => void;
  onError: (message: string) => void;
}) {
  return (
    <>
      <tr className="border-b border-border-light">
        <td className="py-3 pr-3 font-medium text-ink">{row.field_name}</td>
        <td className="py-3 pr-3 text-center">
          <ReportCell
            cell={row.report}
            cellKeyStr={cellKey({ kind: "field_report", fieldId: row.field_id })}
            selectable
            selected={selected}
            onToggle={onToggleCell}
          />
        </td>
        <td className="py-3 pr-3 text-center">
          <ReportCell
            cell={row.roadmap_check}
            cellKeyStr={cellKey({ kind: "roadmap_check", fieldId: row.field_id })}
            selectable={row.roadmap !== null}
            selected={selected}
            onToggle={onToggleCell}
          />
        </td>
        <td className="py-3 whitespace-nowrap">
          <span className="text-xs text-muted">
            {row.roadmap
              ? `${row.roadmap.version_label} · 목표 ${row.roadmap.goal_count}개`
              : "미등록"}
          </span>
          <button type="button" onClick={onToggleOpen} className="ml-2 btn btn-neutral btn-sm">
            {open ? "닫기" : row.roadmap ? "편집" : "등록"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="border-b border-border-light bg-sunken">
          <td colSpan={4} className="py-3 pr-3">
            <RoadmapForm
              fieldId={row.field_id}
              adminKey={adminKey}
              onUnauthorized={onUnauthorized}
              onSaved={onSaved}
              onError={onError}
            />
          </td>
        </tr>
      )}
    </>
  );
}

function RoadmapForm({
  fieldId, adminKey, onUnauthorized, onSaved, onError,
}: {
  fieldId: number;
  adminKey: string;
  onUnauthorized: () => void;
  onSaved: () => void;
  onError: (message: string) => void;
}) {
  const [version, setVersion] = useState("");
  const [content, setContent] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getRoadmap(fieldId, adminKey)
      .then((doc) => {
        setVersion(doc.version_label);
        setContent(doc.content_md);
        setLoaded(true);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) return onUnauthorized();
        onError(e instanceof Error ? e.message : "로드맵을 불러오지 못했습니다.");
      });
  }, [fieldId, adminKey, onUnauthorized, onError]);

  const save = async () => {
    setSaving(true);
    try {
      await putRoadmap(fieldId, { version_label: version, content_md: content }, adminKey);
      onSaved();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      onError(e instanceof Error ? e.message : "저장에 실패했습니다.");
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!confirm("등록된 로드맵을 삭제할까요? 이미 생성된 점검 보고서는 남습니다.")) return;
    try {
      await deleteRoadmap(fieldId, adminKey);
      setVersion("");
      setContent("");
      onSaved();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      onError(e instanceof Error ? e.message : "삭제에 실패했습니다.");
    }
  };

  if (!loaded) return <p className="text-xs text-muted">불러오는 중…</p>;

  return (
    <div className="space-y-3">
      {/* 비공개 판본 여부는 관리자만 판단할 수 있다 — 어디로 나가는지 명시한다.
          임베딩을 로컬화해도 이 문제는 해결되지 않는다(최종 생성이 외부 모델이면
          원문은 프롬프트로 나간다). */}
      <p className="banner banner-warn text-xs">
        ⚠ 여기 저장한 원문은 점검 보고서를 생성할 때 <strong>Gemini API로 전송</strong>됩니다.
        외부로 내보낼 수 없는 판본인지 확인한 뒤 입력하세요.
      </p>

      <label className="block max-w-md text-sm">
        <span className="text-muted">판본</span>
        <input
          value={version}
          onChange={(e) => setVersion(e.target.value)}
          placeholder="2026 제1호 개정"
          className="input mt-1"
        />
      </label>

      <label className="block text-sm">
        <span className="text-muted">
          원문 (마크다운) — 단계별 목표는{" "}
          <code className="bg-sunken px-1 font-sans text-ink">| 단계 | 시기 | 기술적 목표 |</code>{" "}
          형태의 표로 넣습니다. 표가 아니면 저장이 거부됩니다.
        </span>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={12}
          className="input mt-1 w-full"
        />
      </label>

      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={save} disabled={saving} className="btn btn-primary btn-sm">
          {saving ? "저장 중…" : "저장"}
        </button>
        <button type="button" onClick={remove} className="btn btn-danger-quiet btn-sm">
          삭제
        </button>
      </div>
    </div>
  );
}
