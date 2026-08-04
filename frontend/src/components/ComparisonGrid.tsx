import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  getComparisonGrid,
  runAllComparisons,
  type ComparisonGridRow,
} from "../api";
import { COUNTRY_NAMES } from "../lib/countries";

// 관리자 "국가 현황" 격자 — 세부기술 × (국가 분석 · 비교 보고서) 한눈에 보기 +
// 일괄 생성. FieldReportsPanel과 같은 규약(연도 입력 · 일괄 버튼 · busy 상태)을 쓴다.
//
// 개별 조합 큐잉(ComparisonPanel, "국가 비교" 탭)은 그대로 둔다 — 여기는 설정된
// 국가 전체를 정해진 방식(pairs|all)으로만 일괄 큐잉하므로, 임의 조합을 만드는
// 유일한 통로는 여전히 그 탭이다.

// 정렬된 콤마 구분 키 — CountryComparison.countries(백엔드)가 저장하는 형식과 같다.
function comboKey(countries: string[]): string {
  return [...countries].sort().join(",");
}

function StatusMark({
  status,
  blockedTitle,
}: {
  status: string | undefined;
  blockedTitle?: string;
}) {
  if (status === "done") {
    return (
      <span className="text-positive" title="완료" aria-label="완료">
        ●
      </span>
    );
  }
  if (blockedTitle) {
    return (
      <span className="text-faint" title={blockedTitle} aria-label={blockedTitle}>
        —
      </span>
    );
  }
  return (
    <span className="text-muted" title="없음" aria-label="없음">
      ○
    </span>
  );
}

export default function ComparisonGrid({
  adminKey,
  onUnauthorized,
}: {
  adminKey: string;
  onUnauthorized: () => void;
}) {
  const currentYear = new Date().getFullYear();
  const [year, setYear] = useState(currentYear);
  const [countries, setCountries] = useState<string[]>([]);
  const [rows, setRows] = useState<ComparisonGridRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getComparisonGrid(year, adminKey)
      .then((r) => {
        setCountries(r.countries);
        setRows(r.rows);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) return onUnauthorized();
        setError(e instanceof Error ? e.message : "국가 현황을 불러오지 못했습니다.");
      });
  }, [year, adminKey, onUnauthorized]);

  useEffect(load, [load]);

  // 기준국 — 비교의 한쪽을 고정하는 방식(comparison.py::pair_countries)과 같다.
  // KR이 목록에 있으면 KR, 없으면 첫 국가.
  const base = countries.includes("KR") ? "KR" : countries[0];
  const otherCountries = countries.filter((c) => c !== base);
  // 다국 열은 국가가 3개 이상일 때만 — 2개면 1:1 비교와 같은 열이 된다.
  const showMulti = countries.length >= 3;
  const multiKey = comboKey(countries);

  const runAll = async (mode: "pairs" | "all") => {
    const label = mode === "pairs" ? "1:1 비교" : "다국 비교";
    if (
      !confirm(
        `${year}년 전체 세부기술의 ${label}를 일괄 생성할까요?\n\n` +
          "상대국 분석이 없는 세부기술은 건너뜁니다. LLM 호출 비용이 발생합니다.",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await runAllComparisons(year, mode, adminKey);
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
    <section className="mt-6 border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-accent">국가 현황</h2>
        <label className="text-sm text-muted">
          대상 연도{" "}
          <input
            type="number"
            value={year}
            onChange={(e) => setYear(Number(e.target.value))}
            className="input ml-1 w-24"
          />
        </label>
      </div>

      <p className="mt-2 text-xs text-muted">
        열의 국가는 자동 스케줄 설정(대상 국가)을 따릅니다 — 국가를 늘리려면 "자동
        스케줄" 탭에서 먼저 등록해야 합니다. 상대국 분석이 없는 조합은 큐잉해도
        건너뜁니다.
      </p>

      {countries.length < 2 && rows && (
        <p className="mt-3 banner banner-warn">
          설정된 국가가 하나뿐이라 비교를 만들 수 없습니다. "자동 스케줄" 탭에서
          국가를 추가해 주세요.
        </p>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => runAll("pairs")}
          disabled={busy || countries.length < 2}
          className="btn btn-primary btn-sm"
        >
          1:1 비교 일괄 생성
        </button>
        {showMulti && (
          <button
            type="button"
            onClick={() => runAll("all")}
            disabled={busy}
            className="btn btn-secondary btn-sm"
          >
            다국 비교 일괄 생성
          </button>
        )}
        <button type="button" onClick={load} className="btn btn-neutral btn-sm">
          새로고침
        </button>
      </div>

      {error && <p className="mt-3 text-sm text-danger">{error}</p>}

      {rows && (
        <div className="mt-6 table-scroll border-t border-border">
          <table className="w-full border-collapse text-sm">
            <thead className="tbl-head">
              <tr className="border-b border-border">
                <th>세부기술</th>
                {countries.map((c) => (
                  <th key={`a-${c}`}>{COUNTRY_NAMES[c] ?? c} 분석</th>
                ))}
                {otherCountries.map((c) => (
                  <th key={`c-${c}`}>{COUNTRY_NAMES[c] ?? c} 비교</th>
                ))}
                {showMulti && <th>다국 비교</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.subfield_id} className="border-b border-border-light">
                  <td className="py-3 pr-3 font-medium text-ink">{row.subfield_name}</td>
                  {countries.map((c) => (
                    <td key={`a-${c}`} className="py-3 pr-3 text-center">
                      <StatusMark status={row.analyses[c]} />
                    </td>
                  ))}
                  {otherCountries.map((c) => {
                    const status = row.comparisons[comboKey([base, c])];
                    const blocked =
                      status !== "done" &&
                      (row.analyses[base] !== "done" || row.analyses[c] !== "done");
                    return (
                      <td key={`c-${c}`} className="py-3 pr-3 text-center">
                        <StatusMark
                          status={status}
                          blockedTitle={blocked ? "상대국 분석이 없어 불가" : undefined}
                        />
                      </td>
                    );
                  })}
                  {showMulti &&
                    (() => {
                      const status = row.comparisons[multiKey];
                      const blocked =
                        status !== "done" && countries.some((c) => row.analyses[c] !== "done");
                      return (
                        <td className="py-3 text-center">
                          <StatusMark
                            status={status}
                            blockedTitle={blocked ? "상대국 분석이 없어 불가" : undefined}
                          />
                        </td>
                      );
                    })()}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
