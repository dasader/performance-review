import { Fragment, useState } from "react";
import { getMetricPapers, type MetricPaper, type MetricStat } from "../api";

// 정량 지표 분포 — 추출된 수치를 (지표명, 단위)로 묶어 코드가 집계한 표다.
// StatsPanel("기본 통계")이 아니라 **보고서 본문 쪽**에 둔다: 이것은 논문 간 통계
// (기관·저널·인용수 분포)가 아니라 연구 내용 자체의 결과값이라, 서술을 읽은 직후
// "그래서 어느 수준인가"를 잇는 자리가 맞다.
//
// 숫자 열 머리에는 `n` 클래스를 붙인다 — `.tbl-head th`가 text-left를 강제하고
// 유틸리티 text-right는 명시도가 낮아 무시된다(index.css의 `.tbl-head th.n` 참고).
// 이걸 빼면 머리글은 왼쪽, 값은 오른쪽으로 어긋난다.
export default function MetricTable({
  rows,
  unique,
  analysisId,
}: {
  rows: MetricStat[];
  unique: number;
  analysisId: number;
}) {
  // 펼쳐진 지표의 키와 그 논문 목록. 한 번에 하나만 편다 — 여럿 펼치면 표가
  // 수백 줄이 되어 원래 보던 분포를 잃는다.
  const [open, setOpen] = useState<string | null>(null);
  const [papers, setPapers] = useState<MetricPaper[] | null>(null);
  const [loading, setLoading] = useState(false);

  const toggle = (m: MetricStat) => {
    const key = `${m.name}|${m.unit}`;
    if (open === key) {
      setOpen(null);
      return;
    }
    setOpen(key);
    setPapers(null);
    setLoading(true);
    getMetricPapers(analysisId, m.name, m.unit)
      .then((d) => setPapers(d.rows))
      .catch(() => setPapers([]))
      .finally(() => setLoading(false));
  };

  if (!rows.length) return null;
  return (
    <div className="avoid-break table-scroll">
      <h2 className="mb-2 text-xl font-bold tracking-tight text-accent">정량 지표 분포</h2>
      <p className="mb-4 text-sm text-muted">
        추출된 수치를 지표별로 묶어 코드가 전수 집계한 값입니다. 보고서 서술과 달리
        논문 수에 관계없이 모든 수치가 반영됩니다. 최소·최대는 보고된 값의 범위입니다.
        <span className="print:hidden">
          {" "}논문 수를 누르면 그 값들이 어느 논문에서 나왔는지 볼 수 있습니다 — 같은
          지표명 아래 다른 대상이 섞이는 경우가 있어(예: 태양전지 효율과 전력회로
          변환효율) 값이 튀면 여기서 확인합니다.
        </span>
      </p>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="tbl-head">
            <th>지표</th>
            <th className="n">논문 수</th>
            <th className="n">최소</th>
            <th className="n">중앙값</th>
            <th className="n">최대</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <Fragment key={`${m.name}|${m.unit}`}>
            <tr className="border-b border-border-light">
              <td className="py-2 pr-3 text-ink-light">
                {m.name}
                {m.unit && <span className="text-muted"> ({m.unit})</span>}
              </td>
              <td className="py-2 pr-3 text-right text-xs tabular-nums text-muted">
                {/* 인쇄물에서는 누를 수 없으므로 숫자만 남긴다. */}
                <button
                  type="button"
                  onClick={() => toggle(m)}
                  aria-expanded={open === `${m.name}|${m.unit}`}
                  className="underline decoration-dotted underline-offset-2 print:no-underline"
                >
                  {m.count.toLocaleString()}
                </button>
              </td>
              <td className="py-2 pr-3 text-right tabular-nums text-muted">
                {m.min.toLocaleString()}
              </td>
              <td className="py-2 pr-3 text-right tabular-nums">
                {m.median.toLocaleString()}
              </td>
              <td className="py-2 pr-3 text-right tabular-nums text-muted">
                {m.max.toLocaleString()}
              </td>
            </tr>
            {open === `${m.name}|${m.unit}` && (
              <tr className="border-b border-border-light print:hidden">
                <td colSpan={5} className="py-2">
                  {loading && <p className="text-xs text-muted">불러오는 중…</p>}
                  {papers && papers.length === 0 && (
                    <p className="text-xs text-muted">해당 논문을 찾지 못했습니다.</p>
                  )}
                  {papers && papers.length > 0 && (
                    <ul className="text-xs text-muted">
                      {papers.map((p, i) => (
                        <li key={`${p.doi ?? p.title}-${i}`} className="py-1">
                          <span className="tabular-nums text-ink-light">
                            {p.raw ?? p.value}
                          </span>
                          {/* target이 "왜 이 값이 여기 있는가"의 답을 대개 담는다. */}
                          {p.target && <span> · {p.target}</span>}
                          {p.title && (
                            <span>
                              {" · "}
                              {p.doi ? (
                                <a
                                  href={`https://doi.org/${p.doi}`}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="underline"
                                >
                                  {p.title}
                                </a>
                              ) : (
                                p.title
                              )}
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
              </tr>
            )}
            </Fragment>
          ))}
        </tbody>
      </table>
      {unique > 0 && (
        <p className="mt-2 text-xs text-muted">
          여러 논문에 반복 등장한 지표만 싣습니다. 한 논문에만 나온 지표가{" "}
          {unique.toLocaleString()}종 더 있으며, 분포를 낼 수 없어 표에서 제외했습니다.
        </p>
      )}
    </div>
  );
}
