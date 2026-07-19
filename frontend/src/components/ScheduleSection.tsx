import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  get,
  post,
  put,
  type RunNowResponse,
  type ScheduleInfo,
  type ScheduleUpdateIn,
} from "../api";

const TRIGGER_LABEL: Record<string, string> = { scheduled: "정기", manual: "수동" };

export default function ScheduleSection({
  adminKey,
  onUnauthorized,
}: {
  adminKey: string;
  onUnauthorized: () => void;
}) {
  const [data, setData] = useState<ScheduleInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // 편집 폼 초안 — 서버 값을 불러올 때마다 덮어쓴다(저장 후에도 최신 응답으로 재동기화).
  const [draft, setDraft] = useState<ScheduleUpdateIn | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [runningNow, setRunningNow] = useState(false);
  const [runNowError, setRunNowError] = useState<string | null>(null);
  const [runNowResult, setRunNowResult] = useState<RunNowResponse | null>(null);

  const load = useCallback(async () => {
    try {
      const info = await get<ScheduleInfo>("/admin/schedule", adminKey);
      setData(info);
      setDraft({
        enabled: info.enabled,
        day: info.day,
        hour: info.hour,
        years_back: info.years_back,
      });
      setLoadError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      setLoadError(e instanceof Error ? e.message : "스케줄 설정을 불러오지 못했습니다.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminKey]);

  useEffect(() => {
    load();
  }, [load]);

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    setSaveError(null);
    try {
      await put<ScheduleInfo>("/admin/schedule", draft, adminKey);
      await load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      setSaveError(e instanceof Error ? e.message : "스케줄 저장에 실패했습니다.");
    } finally {
      setSaving(false);
    }
  };

  const handleRunNow = async () => {
    const ok = confirm(
      "지금 즉시 활성 세부기술 전체를 강제로 다시 분석합니다(스케줄 시각과 무관).\n" +
        "이미 완료된 연도도 다시 큐잉되며, 이 작업은 취소할 수 없습니다. 계속할까요?",
    );
    if (!ok) return;
    setRunningNow(true);
    setRunNowError(null);
    setRunNowResult(null);
    try {
      const res = await post<RunNowResponse>("/admin/schedule/run-now", {}, adminKey);
      setRunNowResult(res);
      await load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      setRunNowError(e instanceof Error ? e.message : "즉시 실행 요청에 실패했습니다.");
    } finally {
      setRunningNow(false);
    }
  };

  return (
    <section className="mt-6 border border-border bg-surface p-5">
      <h2 className="font-display text-lg font-semibold text-accent">자동 분석 스케줄</h2>
      <p className="mt-1 text-xs text-muted">
        매월 지정한 일·시각에 활성 세부기술 전체를 당해~직전 연도 범위로 자동 재분석합니다.
      </p>

      {loadError && <p className="mt-4 text-sm text-danger">{loadError}</p>}
      {!data && !loadError && <p className="mt-4 text-sm text-muted">불러오는 중…</p>}

      {data && draft && (
        <>
          <div className="mt-4 flex flex-wrap items-end gap-4">
            <div>
              <span id="schedule-enabled-label" className="mb-1 block text-xs font-medium text-ink-light">
                자동 실행
              </span>
              {/* min-w — 켜짐/꺼짐 글자 수가 달라도 토글 폭이 고정되어 눌렀을 때 옆 요소가 밀리지 않는다. */}
              <button
                type="button"
                role="switch"
                aria-checked={draft.enabled}
                aria-labelledby="schedule-enabled-label"
                onClick={() => setDraft((d) => d && { ...d, enabled: !d.enabled })}
                className={`min-w-[4.5rem] border px-2 py-1 text-center text-xs ${
                  draft.enabled ? "border-positive/40 text-positive" : "border-border text-faint"
                }`}
              >
                {draft.enabled ? "켜짐" : "꺼짐"}
              </button>
            </div>

            <div>
              <label htmlFor="schedule-day" className="mb-1 block text-xs font-medium text-ink-light">
                실행 일(1~28)
              </label>
              <input
                id="schedule-day"
                type="number"
                min={1}
                max={28}
                value={draft.day}
                onChange={(e) => setDraft((d) => d && { ...d, day: Number(e.target.value) })}
                className="w-20 border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent"
              />
            </div>

            <div>
              <label htmlFor="schedule-hour" className="mb-1 block text-xs font-medium text-ink-light">
                실행 시(0~23)
              </label>
              <input
                id="schedule-hour"
                type="number"
                min={0}
                max={23}
                value={draft.hour}
                onChange={(e) => setDraft((d) => d && { ...d, hour: Number(e.target.value) })}
                className="w-20 border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent"
              />
            </div>

            <div>
              <label htmlFor="schedule-years-back" className="mb-1 block text-xs font-medium text-ink-light">
                대상 연도 범위(당해 - N, 0~5)
              </label>
              <input
                id="schedule-years-back"
                type="number"
                min={0}
                max={5}
                value={draft.years_back}
                onChange={(e) => setDraft((d) => d && { ...d, years_back: Number(e.target.value) })}
                className="w-20 border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent"
              />
            </div>

            <div>
              <span className="mb-1 block text-xs font-medium text-ink-light">시간대(읽기 전용)</span>
              <p className="border border-border-light bg-paper px-3 py-2 text-sm text-faint">{data.timezone}</p>
            </div>

            <button
              type="button"
              disabled={saving}
              onClick={handleSave}
              className="border border-ink bg-ink px-4 py-2 text-sm font-medium text-paper transition-colors hover:bg-ink/90 disabled:opacity-40"
            >
              {saving ? "저장 중…" : "저장"}
            </button>
          </div>
          {saveError && <p className="mt-2 text-sm text-danger">{saveError}</p>}

          <p className="mt-4 font-mono text-xs tabular-nums text-muted">
            다음 실행 예정 {new Date(data.next_run_at).toLocaleString("ko-KR")} ({data.timezone})
          </p>

          <div className="mt-4 flex items-center gap-3">
            <button
              type="button"
              disabled={runningNow}
              onClick={handleRunNow}
              className="border border-warning/50 px-4 py-2 text-sm font-medium text-warning transition-colors hover:bg-warning/10 disabled:opacity-40"
            >
              {runningNow ? "실행 요청 중…" : "지금 실행"}
            </button>
            {runNowResult && (
              <p className="text-sm text-positive">
                {runNowResult.queued_count.toLocaleString()}건이 대기열에 추가되었습니다.
              </p>
            )}
          </div>
          {runNowError && <p className="mt-2 text-sm text-danger">{runNowError}</p>}

          <h3 className="mt-6 text-sm font-medium text-ink-light">최근 실행 이력</h3>
          {data.history.length === 0 && (
            <p className="mt-2 text-sm text-muted">아직 자동 실행 기록이 없습니다.</p>
          )}
          {data.history.length > 0 && (
            <div className="mt-2 overflow-x-auto border-t border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs text-muted">
                    <th className="py-2 pr-3 font-medium">월</th>
                    <th className="py-2 pr-3 font-medium">실행 시각</th>
                    <th className="py-2 pr-3 font-medium">종류</th>
                    <th className="py-2 pr-3 text-right font-medium">큐잉</th>
                    <th className="py-2 font-medium">성공/실패 요약</th>
                  </tr>
                </thead>
                <tbody>
                  {data.history.map((h) => (
                    <tr key={h.run_month} className="border-b border-border-light">
                      <td className="py-3 pr-3 font-mono text-xs tabular-nums text-ink-light">
                        {h.run_month.slice(0, 7)}
                      </td>
                      <td className="py-3 pr-3 text-xs text-faint">
                        {new Date(h.ran_at).toLocaleString("ko-KR")}
                      </td>
                      <td className="py-3 pr-3 text-xs text-ink-light">{TRIGGER_LABEL[h.trigger] ?? h.trigger}</td>
                      <td className="py-3 pr-3 text-right font-mono text-xs tabular-nums text-muted">
                        {h.queued_count.toLocaleString()}
                      </td>
                      <td className="py-3 text-xs">
                        <span className="text-positive">완료 {h.done_count}</span>
                        {h.failed_count > 0 && <span className="ml-2 text-danger">실패 {h.failed_count}</span>}
                        {h.paused_count > 0 && <span className="ml-2 text-warning">일시중지 {h.paused_count}</span>}
                        {h.in_progress_count > 0 && (
                          <span className="ml-2 text-accent">진행 중 {h.in_progress_count}</span>
                        )}
                        {!h.is_current_snapshot && (
                          <span className="ml-2 text-faint">(이후 실행에 상태 갱신됨)</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}
