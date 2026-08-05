import { useCallback, useEffect, useState } from "react";
import {
  ACTIVE_STATUSES,
  ApiError,
  STATUS_LABEL,
  getComparisonGrid,
  runAllComparisons,
  type ComparisonGridRow,
} from "../api";
import { COUNTRY_NAMES } from "../lib/countries";
import StatusBadge from "./StatusBadge";
import { usePolling } from "../lib/hooks";
import YearInput from "./YearInput";

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

// 백엔드(comparison_grid)가 쌍 칸에 넣는 값. 실행 상태가 아니라 "다국 비교 안에 이미
// 들어 있다"는 표시라 STATUS_LABEL에 넣지 않는다 — 넣으면 진행 상태와 섞여 읽힌다.
const IN_MULTI = "in_multi";

// 이전에는 ●/○/— 세 기호로 done·(그 외 전부)·불가만 구분했는데, "그 외 전부"에
// 실패·진행 중·대기가 모두 뭉개져 실패한 생성이 화면에서 안 보이는 문제가 있었다
// (리뷰 지적). StatusBadge(FieldReportsPanel과 같은 부품)로 바꿔 done/진행 중/실패/
// 일시중지를 점 색으로 구분하고, 상태 행 자체가 없는 "미생성"과 상대국 분석이 없어
// 애초에 만들 수 없는 "—"만 남긴다 — 이 둘은 후속 조치가 다르므로 구분을 유지한다
// (미생성 → 지금 큐잉하면 됨, — → 상대국부터 분석해야 함).
function StatusCell({
  status,
  blockedTitle,
}: {
  status: string | undefined;
  blockedTitle?: string;
}) {
  // 실행 상태가 아니라 "이미 다국 비교 안에 들어 있다"는 사실이라 점 배지를 쓰지 않는다.
  // 3개국 이상 비교는 한국 vs 각 나라 1:1을 먼저 만들어 담고 그것을 종합하므로,
  // 이 칸을 "미생성"으로 두면 이미 있는 내용을 다시 만들게 된다.
  if (status === IN_MULTI) {
    const title = "다국 비교 안에 1:1 대조로 들어 있습니다 — 따로 만들 필요가 없습니다.";
    return (
      <span className="text-xs text-muted" title={title}>
        다국에 포함
      </span>
    );
  }
  if (status) {
    return <StatusBadge status={status} label={STATUS_LABEL[status] ?? status} />;
  }
  if (blockedTitle) {
    return (
      <span className="text-faint" title={blockedTitle} aria-label={blockedTitle}>
        —
      </span>
    );
  }
  return <span className="text-xs text-muted">미생성</span>;
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

  // 일괄 실행(최대 1년치 55분) 뒤 화면이 굳어 있지 않도록, 뭔가 진행 중이면
  // FieldReportsPanel과 같은 규약(5초 폴링)으로 다시 읽는다.
  const hasPending =
    rows?.some(
      (r) =>
        Object.values(r.analyses).some((s) => ACTIVE_STATUSES.has(s)) ||
        Object.values(r.comparisons).some((s) => s === "pending"),
    ) ?? false;
  usePolling(hasPending, load);

  // 열은 설정된 국가만으로 만들면 설정에 없는 국가의 기존 분석·비교가 화면에서
  // 사라진다(리뷰 지적 — 예: 설정은 KR뿐인데 CN 분석·KR-CN 비교가 이미 있는 경우).
  // 설정된 국가 + 행에 실제로 존재하는 국가(분석 키·비교 키를 콤마로 쪼갠 것)의
  // 합집합으로 열을 만들되, 설정된 국가를 앞에 두어 운영자가 의도한 집합이 먼저
  // 보이게 한다. 일괄 생성 버튼은 이 합집합이 아니라 설정된 국가만 대상으로 한다
  // (아래 runAll — 백엔드가 큐잉 시 스케줄 설정을 다시 읽으므로 버튼은 그 집합과만
  // 일치해야 혼란이 없다).
  const present = new Set<string>();
  for (const row of rows ?? []) {
    for (const c of Object.keys(row.analyses)) present.add(c);
    for (const combo of Object.keys(row.comparisons)) {
      for (const c of combo.split(",")) present.add(c);
    }
  }
  const extraCountries = [...present].filter((c) => !countries.includes(c)).sort();
  const allCountries = [...countries, ...extraCountries];

  // 기준국 — 비교의 한쪽을 고정하는 방식(comparison.py::pair_countries)과 같다.
  // **정렬 후** KR이 있으면 KR, 없으면 정렬된 첫 국가 — pair_countries도 정렬된
  // 목록을 받는다(enqueue_comparison이 국가를 정렬해 저장). 여기서 정렬하지 않고
  // 설정 순서(countries[0])를 그대로 쓰면, 예를 들어 설정이 "US,CN,JP"(KR 없음)일 때
  // 프론트는 US를 기준으로 CN·JP와 짝짓지만 백엔드는 정렬된 첫 국가 CN을 기준으로
  // JP·US와 짝지어, US-JP 조합을 영영 "미생성"으로 잘못 표시한다(리뷰 지적 버그).
  const sortedAll = [...allCountries].sort();
  const base = sortedAll.includes("KR") ? "KR" : sortedAll[0];
  const otherCountries = allCountries.filter((c) => c !== base);
  // 다국 열은 국가가 3개 이상일 때만 — 2개면 1:1 비교와 같은 열이 된다. 열 표시는
  // 합집합 기준(이미 존재하는 다국 비교를 보여줘야 하므로), 버튼은 아래에서 따로
  // 설정된 국가 기준으로 판단한다.
  const showMultiColumn = allCountries.length >= 3;
  const multiKey = comboKey(allCountries);
  // 다국 비교 일괄 생성 버튼은 설정된 국가 집합에서만 의미가 있다(백엔드가 그 집합을
  // 다시 읽어 큐잉하므로).
  const showMultiButton = countries.length >= 3;

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
        <YearInput year={year} onChange={setYear} />
      </div>

      <p className="mt-2 text-xs text-muted">
        열은 자동 스케줄 설정(대상 국가)에 실제로 분석·비교가 있는 다른 국가까지
        더해서 보여줍니다 — 이미 만들어진 결과는 설정과 무관하게 항상 보입니다.
        <strong className="text-ink"> 일괄 생성 버튼은 설정된 국가만</strong> 대상으로
        하므로, 설정에 없는 국가는 "국가 비교" 탭에서 개별 큐잉해야 합니다. 상대국
        분석이 없는 조합은 큐잉해도 건너뜁니다.
      </p>
      <p className="mt-1 text-xs text-muted">
        점 배지는 실행 상태(완료·진행 중·실패·일시중지), "미생성"은 아직 큐잉되지 않음,
        —는 상대국 분석이 없어 지금은 만들 수 없음을 뜻합니다.{" "}
        <strong className="text-ink">"다국에 포함"</strong>은 그 1:1 대조가 다국 비교
        안에 이미 들어 있다는 뜻으로, 따로 만들면 같은 내용을 다시 만드는 것입니다.
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
        {showMultiButton && (
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
                {allCountries.map((c) => (
                  <th key={`a-${c}`}>{COUNTRY_NAMES[c] ?? c} 분석</th>
                ))}
                {otherCountries.map((c) => (
                  <th key={`c-${c}`}>{COUNTRY_NAMES[c] ?? c} 비교</th>
                ))}
                {showMultiColumn && <th>다국 비교</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.subfield_id} className="border-b border-border-light">
                  <td className="py-3 pr-3 font-medium text-ink">{row.subfield_name}</td>
                  {allCountries.map((c) => (
                    <td key={`a-${c}`} className="py-3 pr-3 text-center">
                      <StatusCell status={row.analyses[c]} />
                    </td>
                  ))}
                  {otherCountries.map((c) => {
                    const status = row.comparisons[comboKey([base, c])];
                    const blocked =
                      status !== "done" &&
                      (row.analyses[base] !== "done" || row.analyses[c] !== "done");
                    return (
                      <td key={`c-${c}`} className="py-3 pr-3 text-center">
                        <StatusCell
                          status={status}
                          blockedTitle={blocked ? "상대국 분석이 없어 불가" : undefined}
                        />
                      </td>
                    );
                  })}
                  {showMultiColumn &&
                    (() => {
                      const status = row.comparisons[multiKey];
                      const blocked =
                        status !== "done" && allCountries.some((c) => row.analyses[c] !== "done");
                      return (
                        <td className="py-3 text-center">
                          <StatusCell
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
