import { useEffect, useRef, useState } from "react";
import { ApiError, post, type PreviewResponse, type RunResponse } from "../api";

// 미리보기와 실행은 항상 같은 (subfieldId, yearFrom, yearTo, force) 조합을 가리켜야 한다.
// 값이 하나라도 바뀌면 이전 미리보기는 더 이상 실행 근거가 될 수 없으므로 즉시 버린다 —
// "미리보기 없이 실행" 버튼이 눌리는 경로를 원천적으로 막는 장치.
export default function RunDialog({
  adminKey,
  rows,
  defaultYearFrom,
  defaultYearTo,
  subfieldsVersion,
  onRan,
  onUnauthorized,
}: {
  adminKey: string;
  rows: { subfield_id: number; subfield_name: string }[];
  defaultYearFrom: number;
  defaultYearTo: number;
  // 세부기술 검색식이 바뀔 때마다(SubfieldEditor의 onChanged) Admin.tsx가 증가시키는 세대 카운터.
  // 이 화면 자체 입력과 무관하게 값이 바뀌면 미리보기가 stale해진 것이므로 폐기한다.
  subfieldsVersion: number;
  onRan: () => void;
  onUnauthorized: () => void;
}) {
  const [subfieldId, setSubfieldId] = useState<number | "">("");
  const [yearFrom, setYearFrom] = useState(defaultYearFrom);
  const [yearTo, setYearTo] = useState(defaultYearTo);
  const [force, setForce] = useState(false);

  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [runResult, setRunResult] = useState<RunResponse | null>(null);
  const [staleNotice, setStaleNotice] = useState<string | null>(null);

  const invalidate = () => {
    setPreview(null);
    setPreviewError(null);
    setRunResult(null);
    setRunError(null);
    setStaleNotice(null);
  };

  // 세부기술 검색식이 (이 화면 밖에서) 바뀌면 확인했던 숫자가 더 이상 유효하지 않다.
  // 첫 렌더에서는 건너뛰고, 이후 세대 값이 바뀔 때만 폐기 + 사유 안내.
  const isFirstGen = useRef(true);
  useEffect(() => {
    if (isFirstGen.current) {
      isFirstGen.current = false;
      return;
    }
    invalidate();
    setStaleNotice("검색식이 변경되어 미리보기를 다시 실행해야 합니다.");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subfieldsVersion]);

  const selectedName = rows.find((r) => r.subfield_id === subfieldId)?.subfield_name ?? "";

  const handlePreview = async () => {
    if (subfieldId === "") {
      setPreviewError("세부기술을 선택하세요.");
      return;
    }
    if (yearFrom > yearTo) {
      setPreviewError("시작 연도가 종료 연도보다 클 수 없습니다.");
      return;
    }
    setPreviewing(true);
    setPreviewError(null);
    setRunResult(null);
    setStaleNotice(null);
    try {
      setPreview(
        await post<PreviewResponse>(
          "/admin/preview",
          { subfield_id: subfieldId, year_from: yearFrom, year_to: yearTo },
          adminKey,
        ),
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      setPreviewError(e instanceof Error ? e.message : "미리보기에 실패했습니다.");
    } finally {
      setPreviewing(false);
    }
  };

  const handleRun = async () => {
    if (!preview || preview.over_limit || subfieldId === "") return;
    const ok = confirm(
      `'${selectedName}' ${yearFrom}–${yearTo}년 분석을 실행합니다.\n` +
        `예상 총비용 약 $${preview.estimated_total_cost_usd.toFixed(4)} ` +
        `(OpenAlex $${preview.estimated_cost_usd.toFixed(4)} + LLM 추정 $${preview.estimated_llm_cost_usd.toFixed(4)}).\n` +
        `LLM 비용은 논문당 평균 토큰 근사치 기반 추정치입니다. 이 작업은 취소할 수 없습니다. 계속할까요?`,
    );
    if (!ok) return;
    setRunning(true);
    setRunError(null);
    try {
      const res = await post<RunResponse>(
        "/admin/run",
        { subfield_ids: [subfieldId], year_from: yearFrom, year_to: yearTo, force },
        adminKey,
      );
      setRunResult(res);
      setPreview(null);
      onRan();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      setRunError(e instanceof Error ? e.message : "실행 요청에 실패했습니다.");
    } finally {
      setRunning(false);
    }
  };

  return (
    <section className="mt-6 border border-border bg-surface p-5">
      <h2 className="font-display text-lg font-semibold text-ink">분석 실행</h2>
      <p className="mt-1 text-xs text-muted">
        미리보기는 검색만 수행하며 <span className="font-medium text-ink-light">LLM은 호출하지 않지만, OpenAlex 검색 비용(약 $0.002)이 소량 발생합니다.</span>{" "}
        실행 전 반드시 미리보기로 건수와 예상 비용을 확인하세요.
      </p>

      <div className="mt-4 flex flex-wrap items-end gap-2">
        <div>
          <label htmlFor="run-subfield" className="mb-1 block text-xs font-medium text-ink-light">
            세부기술
          </label>
          <select
            id="run-subfield"
            value={subfieldId}
            onChange={(e) => {
              setSubfieldId(e.target.value ? Number(e.target.value) : "");
              invalidate();
            }}
            className="border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent"
          >
            <option value="">세부기술 선택</option>
            {rows.map((r) => (
              <option key={r.subfield_id} value={r.subfield_id}>
                {r.subfield_name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="run-year-from" className="mb-1 block text-xs font-medium text-ink-light">
            시작 연도
          </label>
          <input
            id="run-year-from"
            type="number"
            min={1900}
            max={2100}
            value={yearFrom}
            onChange={(e) => {
              setYearFrom(Number(e.target.value));
              invalidate();
            }}
            className="w-24 border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent"
          />
        </div>
        <div>
          <label htmlFor="run-year-to" className="mb-1 block text-xs font-medium text-ink-light">
            종료 연도
          </label>
          <input
            id="run-year-to"
            type="number"
            min={1900}
            max={2100}
            value={yearTo}
            onChange={(e) => {
              setYearTo(Number(e.target.value));
              invalidate();
            }}
            className="w-24 border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent"
          />
        </div>
        <label className="flex items-center gap-2 pb-2 text-sm text-ink-light">
          <input
            type="checkbox"
            checked={force}
            onChange={(e) => {
              setForce(e.target.checked);
              invalidate();
            }}
          />
          이미 완료된 연도도 강제로 다시 실행
        </label>
        <button
          type="button"
          disabled={subfieldId === "" || previewing}
          onClick={handlePreview}
          className="border border-ink px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-ink hover:text-paper disabled:opacity-40"
        >
          {previewing ? "확인 중…" : "미리보기"}
        </button>
      </div>
      {staleNotice && <p className="mt-3 text-sm text-warning">{staleNotice}</p>}
      {previewError && <p className="mt-3 text-sm text-danger">{previewError}</p>}

      {preview && (
        <div className="mt-5 border border-border-light bg-paper p-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <PreviewTile label="OpenAlex 전체 건수" value={preview.openalex_count.toLocaleString()} />
            <PreviewTile
              label="KCI 표본 건수"
              value={`${preview.kci_sample_count.toLocaleString()}${preview.kci_sample_truncated ? "+" : ""}`}
              caption={
                preview.kci_sample_truncated
                  ? "표본 상한 20건에 도달 — 실제 건수는 더 많을 수 있음"
                  : "표본 상한 20건 이내 전수"
              }
            />
            <PreviewTile label="예상 호출" value={`${preview.estimated_pages.toLocaleString()}콜`} />
            <PreviewTile
              label="추출 대상(추정)"
              value={preview.estimated_papers_to_extract.toLocaleString()}
              caption="캐시 히트를 빼지 않은 상한선"
            />
          </div>

          <div className="mt-3 border border-warning/50 bg-warning/5 px-3 py-2">
            <p className="text-xs text-ink-light">
              예상 총비용 <span className="font-medium">(추정치)</span>
            </p>
            <p className="mt-0.5 font-mono text-2xl font-semibold tabular-nums text-ink">
              ${preview.estimated_total_cost_usd.toFixed(4)}
            </p>
            <p className="mt-0.5 text-xs text-faint">
              OpenAlex ${preview.estimated_cost_usd.toFixed(4)} (실측 단가) + LLM(map) $
              {preview.estimated_llm_cost_usd.toFixed(4)} (논문당 평균 토큰 근사치 기반 추정 — 실제와
              다를 수 있음)
            </p>
          </div>

          <p className="mt-3 font-mono text-xs tabular-nums text-muted">
            OpenAlex 오늘 사용 ${preview.budget_spent.toFixed(4)} / ${preview.budget_limit.toFixed(2)}
          </p>

          {preview.over_limit && (
            <p className="mt-3 border border-danger/40 bg-danger/5 px-3 py-2 text-sm text-danger">
              검색 결과가 처리 상한 {preview.max_papers.toLocaleString()}건을 초과합니다. 검색식을
              좁히거나 세부기술을 분할한 뒤 다시 미리보기하세요. 이 상태로는 실행할 수 없습니다.
            </p>
          )}

          {preview.samples.length > 0 && (
            <>
              <p className="mt-4 text-xs font-medium text-ink-light">표본 미리보기</p>
              <ul className="mt-1 space-y-1 text-xs text-muted">
                {preview.samples.slice(0, 5).map((s, i) => (
                  <li key={i}>
                    [{s.year}] {s.title}
                    {s.journal && <span className="text-faint"> · {s.journal}</span>}
                    {!s.has_abstract && <span className="ml-1 text-warning">(abstract 없음)</span>}
                  </li>
                ))}
              </ul>
            </>
          )}

          <button
            type="button"
            disabled={preview.over_limit || running}
            onClick={handleRun}
            className="mt-4 border border-ink bg-ink px-4 py-2 text-sm font-medium text-paper transition-colors hover:bg-ink/90 disabled:opacity-40"
          >
            {running ? "실행 요청 중…" : "이 내용으로 분석 실행"}
          </button>
        </div>
      )}

      {runError && <p className="mt-3 text-sm text-danger">{runError}</p>}

      {runResult && (
        <div className="mt-4 border border-positive/40 bg-positive/5 p-4 text-sm">
          <p className="font-medium text-positive">
            대기열에 추가되었습니다 ({runResult.queued.length}건). 실행 상태 표에서 진행 상황을
            확인할 수 있습니다.
          </p>
          {runResult.blocked.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs text-warning">
              {runResult.blocked.map((b) => (
                <li key={b.subfield_id}>
                  세부기술 #{b.subfield_id}: {b.reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

function PreviewTile({ label, value, caption }: { label: string; value: string; caption?: string }) {
  return (
    <div className="border border-border-light bg-surface px-3 py-2">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-0.5 font-mono text-lg tabular-nums text-ink">{value}</p>
      {caption && <p className="mt-0.5 text-xs text-faint">{caption}</p>}
    </div>
  );
}
