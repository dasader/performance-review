import { useCallback, useEffect, useState } from "react";
import Switch from "./Switch";
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

// 기본으로 보여줄 실행 이력 건수. 나머지(최대 서버 상한 12건)는 "더 보기"로 펼친다 —
// 표가 늘어질수록 위의 상태/설정 카드가 아래로 밀려 화면 첫인상이 산만해지기 때문.
const HISTORY_COLLAPSED_COUNT = 3;

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

  const [historyExpanded, setHistoryExpanded] = useState(false);

  const load = useCallback(async () => {
    try {
      const info = await get<ScheduleInfo>("/admin/schedule", adminKey);
      setData(info);
      setDraft({
        enabled: info.enabled,
        day: info.day,
        hour: info.hour,
        years_back: info.years_back,
        countries: info.countries,
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

  // 우측 상단 배지는 "저장된 값"이고 토글·숫자 입력은 "초안"이라, 저장 전에는 둘이
  // 어긋난다 — 표시가 없으면 "토글을 눌렀는데 배지가 안 바뀐다"로 읽힌다.
  const dirty =
    !!data &&
    !!draft &&
    (draft.enabled !== data.enabled ||
      draft.day !== data.day ||
      draft.hour !== data.hour ||
      draft.years_back !== data.years_back ||
      draft.countries !== data.countries);

  const visibleHistory = data
    ? historyExpanded
      ? data.history
      : data.history.slice(0, HISTORY_COLLAPSED_COUNT)
    : [];

  return (
    <section className="mt-6 border border-border bg-surface p-4">
      {/* 헤더 — 제목과 함께 "지금 이 순간의 상태"(활성 여부 + 다음 실행 시각)를 먼저 보여준다.
          아래 설정 편집·실행 이력은 이 상태에 종속된 세부 정보다. */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-accent">자동 분석 스케줄</h2>
          <p className="mt-1 text-xs text-muted">
            매월 지정한 일·시각에 활성 세부기술 전체를 당해~직전 연도 범위로 자동 재분석합니다.
          </p>
        </div>
        {data && (
          <div className="shrink-0 border border-border-light bg-paper px-4 py-2">
            <p className="flex items-center justify-end gap-2 text-xs font-medium text-ink-light">
              <span
                aria-hidden="true"
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${data.enabled ? "bg-positive-mark" : "bg-border-strong"}`}
              />
              자동 실행 {data.enabled ? "켜짐" : "꺼짐"}
            </p>
            <p className="mt-1 text-right text-xs tabular-nums text-muted">
              다음 실행 {new Date(data.next_run_at).toLocaleString("ko-KR")} ({data.timezone})
            </p>
            {dirty && (
              <p className="mt-1 text-right text-xs text-warning">
                저장되지 않은 변경 — 설정 저장을 눌러야 반영됩니다
              </p>
            )}
          </div>
        )}
      </div>

      {loadError && <p className="mt-4 text-sm text-danger">{loadError}</p>}
      {!data && !loadError && <p className="mt-4 text-sm text-muted">불러오는 중…</p>}

      {data && draft && (
        <>
          {/* 설정 편집 */}
          <div className="mt-4">
            <h3 className="text-sm font-medium text-ink-light">설정 편집</h3>
            <div className="mt-3 flex flex-wrap items-end gap-4">
              <div>
                <span id="schedule-enabled-label" className="mb-1 block text-xs font-medium text-ink-light">
                  자동 실행
                </span>
                {/* 이전에는 테두리만 있는 초록 버튼이었다 — ① 테두리만 있는 버튼은 지면 위에서
                    눌리는 것으로 읽히지 않고 ② 켬/끔은 상태 4단이 아니라 상태색을 쓸 자리가 아니다.
                    세부기술 표의 활성 토글과 같은 스위치를 써서 켬/끔 표현을 화면 전체에서 통일한다. */}
                <div className="flex h-8 items-center">
                  <Switch
                    checked={draft.enabled}
                    onChange={() => setDraft((d) => d && { ...d, enabled: !d.enabled })}
                    label={draft.enabled ? "켜짐" : "꺼짐"}
                    ariaLabel="자동 실행"
                  />
                </div>
              </div>

              <ScheduleNumberField
                id="schedule-day"
                label="실행 일(1~28)"
                min={1}
                max={28}
                value={draft.day}
                onChange={(v) => setDraft((d) => d && { ...d, day: v })}
              />
              <ScheduleNumberField
                id="schedule-hour"
                label="실행 시(0~23)"
                min={0}
                max={23}
                value={draft.hour}
                onChange={(v) => setDraft((d) => d && { ...d, hour: v })}
              />
              <ScheduleNumberField
                id="schedule-years-back"
                label="대상 연도 범위(당해 - N, 0~5)"
                min={0}
                max={5}
                value={draft.years_back}
                onChange={(v) => setDraft((d) => d && { ...d, years_back: v })}
              />

              <div>
                <label
                  htmlFor="schedule-countries"
                  className="mb-1 block text-xs font-medium text-ink-light"
                >
                  대상 국가(콤마 구분)
                </label>
                <input
                  id="schedule-countries"
                  className="input"
                  value={draft.countries}
                  onChange={(e) =>
                    setDraft((d) => d && { ...d, countries: e.target.value })
                  }
                  placeholder="KR,US,CN"
                />
                <p className="mt-1 text-xs text-muted">
                  국가마다 검색·추출이 따로 돌아 비용이 곱해집니다. 기본은 KR입니다.
                </p>
              </div>

              <div>
                <span className="mb-1 block text-xs font-medium text-ink-light">시간대(읽기 전용)</span>
                {/* 읽기 전용이지만 옆의 숫자 입력들과 한 줄에 서므로 .input 계약을 그대로
                    쓴다 — 높이·모서리·좌우 여백을 손으로 다시 조립하면 조용히 어긋난다.
                    바탕만 눌린 면으로 내려 "누를 수 없는 칸"임을 밝힌다. */}
                <p className="input flex w-auto items-center bg-sunken text-muted">
                  {data.timezone}
                </p>
              </div>
            </div>

            {/* 두 버튼을 한 줄에 세로 정렬한다. 성격이 다르므로(값 저장 vs 되돌릴 수 없는
                실행) 좌우로 갈라 놓고, 즉시 실행 쪽에만 경고색 테두리를 줘 구분한다.
                박스로 감싸면 카드 안에 카드가 생겨 오히려 산만해진다. */}
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
              <button
                type="button"
                disabled={saving || !dirty}
                onClick={handleSave}
                className="btn btn-primary"
              >
                {saving ? "저장 중…" : dirty ? "설정 저장" : "저장됨"}
              </button>
              <button
                type="button"
                disabled={runningNow}
                onClick={handleRunNow}
                className="btn btn-secondary"
              >
                {runningNow ? "실행 요청 중…" : "지금 실행"}
              </button>
            </div>

            <p className="mt-2 text-right text-xs text-muted">
              지금 실행은 스케줄 시각과 무관하게 활성 세부기술 전체를 재분석하며 되돌릴 수 없습니다.
            </p>

            {saveError && <p className="mt-2 text-sm text-danger">{saveError}</p>}
            {runNowResult && (
              <p className="mt-2 text-right text-sm text-positive">
                {runNowResult.queued_count.toLocaleString()}건이 대기열에 추가되었습니다.
              </p>
            )}
            {runNowError && <p className="mt-2 text-right text-sm text-danger">{runNowError}</p>}
          </div>

          {/* 실행 이력 */}
          <div className="mt-4 border-t border-border pt-4">
            <h3 className="text-sm font-medium text-ink-light">최근 실행 이력</h3>
            {data.history.length === 0 && (
              <p className="mt-2 text-sm text-muted">아직 자동 실행 기록이 없습니다.</p>
            )}
            {data.history.length > 0 && (
              <>
                <div className="mt-2 table-scroll">
                  <table className="w-full border-collapse text-sm">
                    <thead className="tbl-head">
                      <tr className="border-b border-border-light">
                        <th>월</th>
                        <th>실행 시각</th>
                        <th>종류</th>
                        <th className="n">큐잉</th>
                        <th>성공/실패 요약</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleHistory.map((h) => (
                        <tr key={h.run_month}>
                          <td className="py-3 pr-3 text-xs tabular-nums text-ink-light">
                            {h.run_month.slice(0, 7)}
                          </td>
                          <td className="py-3 pr-3 text-xs text-faint">
                            {new Date(h.ran_at).toLocaleString("ko-KR")}
                          </td>
                          <td className="py-3 pr-3 text-xs text-ink-light">
                            {TRIGGER_LABEL[h.trigger] ?? h.trigger}
                          </td>
                          <td className="py-3 pr-3 text-right text-xs tabular-nums text-muted">
                            {h.queued_count.toLocaleString()}
                          </td>
                          <td className="py-3 text-xs">
                            <span className="text-positive">완료 {h.done_count}</span>
                            {h.failed_count > 0 && <span className="ml-2 text-danger">실패 {h.failed_count}</span>}
                            {h.paused_count > 0 && (
                              <span className="ml-2 text-warning">일시중지 {h.paused_count}</span>
                            )}
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
                {data.history.length > HISTORY_COLLAPSED_COUNT && (
                  <button
                    type="button"
                    onClick={() => setHistoryExpanded((v) => !v)}
                    className="mt-2 text-xs font-medium text-accent hover:underline"
                  >
                    {historyExpanded
                      ? "접기"
                      : `이전 이력 더 보기 (${data.history.length - HISTORY_COLLAPSED_COUNT}건)`}
                  </button>
                )}
              </>
            )}
          </div>
        </>
      )}
    </section>
  );
}

// 실행 일(1~28)/실행 시(0~23)/대상 연도 범위(0~5) — 셋 다 두 자리 이내 숫자라 폭을
// 하나로 통일한다(w-16). RunDialog의 연도 입력(w-24, 네 자리)과는 자릿수가 다르므로
// 폭도 다르게 가져가는 게 "내용에 맞는 폭"이라는 원칙에 맞다.
function ScheduleNumberField({
  id,
  label,
  min,
  max,
  value,
  onChange,
}: {
  id: string;
  label: string;
  min: number;
  max: number;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-ink-light">
        {label}
      </label>
      <input
        id={id}
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-16 input"
      />
    </div>
  );
}
