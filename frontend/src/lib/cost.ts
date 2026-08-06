import { cellKey, type QueueRequestBody } from "./selection";

// 실측 단가(2026-08-05, 데이터·AI 보안 2025 KR 489건 · 바이오 데이터·AI 2026 KR 501건).
// gemini-3.1-flash-lite / thinking=high 기준이다.
//
// **모델이나 THINKING_REDUCE를 바꾸면 이 값도 손봐야 한다** — 같은 측정에서
// gemini-3.6-flash는 단일 reduce가 7배, 3단 reduce가 8배였다.
// 설정으로 빼지 않는 이유: 어차피 추정치이고, 설정으로 만들면 관리 지점만 는다.
export const COMPARISON_USD = 0.05;
export const FIELD_REPORT_USD = 0.03;

export interface CostEstimate {
  /** 보고서류 예상 금액(USD). 분석은 포함하지 않는다. */
  reportUsd: number;
  /** 선택된 분석 건수. */
  analysisCount: number;
  /** 참고 논문 수 합계. 과거 실적이 하나도 없으면 null. */
  analysisPapers: number | null;
}

// 분석 비용을 금액으로 내지 않는 이유: /admin/preview가 OpenAlex를 실제로 호출해야
// 건수를 알 수 있고 그 호출 자체가 과금이다. 셀을 고를 때마다 돈이 나가는 화면은
// 만들 수 없다. 대신 같은 셀의 과거 검색 건수를 참고값으로 보여준다.
export function estimateCost(
  body: QueueRequestBody,
  papersByCell: Record<string, number>,
): CostEstimate {
  const reportUsd =
    body.comparisons.length * COMPARISON_USD +
    (body.field_reports.length + body.roadmap_checks.length) * FIELD_REPORT_USD;

  let papers: number | null = null;
  for (const item of body.analyses) {
    const known = papersByCell[
      cellKey({ kind: "analysis", subfieldId: item.subfield_id, country: item.country })
    ];
    if (known !== undefined) papers = (papers ?? 0) + known;
  }

  return { reportUsd, analysisCount: body.analyses.length, analysisPapers: papers };
}
