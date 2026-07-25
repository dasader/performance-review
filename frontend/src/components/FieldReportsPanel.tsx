import { useCallback, useEffect, useState } from "react";
import { ApiError, get, post } from "../api";
import { formatGeneratedAt } from "../lib/format";

// 관리자 "분야 보고서" 탭 — 당해연도 전체를 일괄 큐잉하고, 분야별 종합/점검 상태를
// 한눈에 본다. 실제 생성은 잡 루프가 한 틱에 하나씩 하므로, 이 화면은 큐잉만 걸고
// 주기적으로 현황을 다시 읽어 pending → done 진행을 보여준다.

interface Cell {
  status: "pending" | "done" | "failed";
  source_count: number;
  generated_at: string | null;
  error: string | null;
}
interface Row {
  field_id: number;
  field_name: string;
  has_roadmap: boolean;
  report: Cell | null;
  roadmap_check: Cell | null;
}

const STATUS_LABEL: Record<string, string> = {
  pending: "대기 중",
  done: "완료",
  failed: "실패",
};

function StatusChip({ cell, eligible }: { cell: Cell | null; eligible: boolean }) {
  if (!cell) {
    return <span className="text-xs text-faint">{eligible ? "미생성" : "대상 아님"}</span>;
  }
  const cls =
    cell.status === "done"
      ? "text-positive"
      : cell.status === "failed"
        ? "text-danger"
        : "text-warning";
  return (
    <span className={`text-xs font-medium ${cls}`}>
      ● {STATUS_LABEL[cell.status] ?? cell.status}
      {cell.status === "done" && cell.generated_at && (
        <span className="ml-1 font-normal text-faint">{formatGeneratedAt(cell.generated_at)}</span>
      )}
    </span>
  );
}

export default function FieldReportsPanel({
  adminKey,
  onUnauthorized,
}: {
  adminKey: string;
  onUnauthorized: () => void;
}) {
  const currentYear = new Date().getFullYear();
  const [year, setYear] = useState(currentYear);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    get<{ rows: Row[] }>(`/admin/field-reports?year=${year}`, adminKey)
      .then((r) => setRows(r.rows))
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) return onUnauthorized();
        setError(e.message);
      });
  }, [year, adminKey, onUnauthorized]);

  useEffect(load, [load]);

  // pending이 하나라도 있으면 진행 중이므로 주기적으로 현황을 다시 읽는다.
  const hasPending =
    rows?.some((r) => r.report?.status === "pending" || r.roadmap_check?.status === "pending") ??
    false;
  useEffect(() => {
    if (!hasPending) return;
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [hasPending, load]);

  const runAll = async (kind: "report" | "roadmap-check") => {
    const label = kind === "report" ? "분야 종합보고서" : "로드맵 이행 점검";
    if (
      !confirm(
        `${year}년 전체 분야의 ${label}를 일괄 생성할까요?\n\n` +
          "대상 분야가 큐에 등록되어 순서대로 생성되며, LLM 호출 비용이 발생합니다.",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await post<{ queued: number; skipped: number }>(
        `/admin/field-reports/run-all?year=${year}&kind=${kind}`,
        null,
        adminKey,
      );
      alert(`${label}: ${r.queued}건 큐잉, ${r.skipped}건 건너뜀(대상 아님).`);
      load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      setError(e instanceof Error ? e.message : "일괄 실행 요청에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-6 border border-border bg-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-display text-lg font-semibold text-accent">분야 보고서 일괄 생성</h2>
        <label className="text-sm text-muted">
          대상 연도{" "}
          <input
            type="number"
            value={year}
            onChange={(e) => setYear(Number(e.target.value))}
            className="ml-1 w-24 border border-border bg-paper px-2 py-1 text-sm text-ink"
          />
        </label>
      </div>

      <p className="mt-2 text-xs text-muted">
        완성된 세부기술 보고서가 있는 분야만 대상입니다(로드맵 점검은 로드맵이 등록된 분야).
        생성은 잡 루프가 한 틱에 하나씩 처리하므로 완료까지 시간이 걸립니다.
      </p>

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => runAll("report")}
          disabled={busy}
          className="btn btn-primary btn-sm"
        >
          종합보고서 전체 생성
        </button>
        <button
          type="button"
          onClick={() => runAll("roadmap-check")}
          disabled={busy}
          className="btn btn-primary btn-sm"
        >
          로드맵 점검 전체 생성
        </button>
        <button type="button" onClick={load} className="btn btn-neutral btn-sm">
          새로고침
        </button>
      </div>

      {error && <p className="mt-3 text-sm text-danger">{error}</p>}

      {rows && (
        <div className="mt-6 overflow-x-auto border-t border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted">
                <th className="py-2 pr-3 font-medium">분야</th>
                <th className="py-2 pr-3 font-medium">종합보고서</th>
                <th className="py-2 pr-3 font-medium">로드맵 점검</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.field_id} className="border-b border-border-light">
                  <td className="py-3 pr-3 font-medium text-ink">{r.field_name}</td>
                  <td className="py-3 pr-3">
                    {/* 종합보고서는 세부기술 보고서만 있으면 대상이다. source_count로
                        완성 여부를 판단할 순 없으니(미생성은 셀=null) 항상 eligible 처리. */}
                    <StatusChip cell={r.report} eligible />
                    {r.report?.status === "failed" && r.report.error && (
                      <p className="mt-1 max-w-xs text-xs text-danger">{r.report.error}</p>
                    )}
                  </td>
                  <td className="py-3 pr-3">
                    <StatusChip cell={r.roadmap_check} eligible={r.has_roadmap} />
                    {r.roadmap_check?.status === "failed" && r.roadmap_check.error && (
                      <p className="mt-1 max-w-xs text-xs text-danger">{r.roadmap_check.error}</p>
                    )}
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
