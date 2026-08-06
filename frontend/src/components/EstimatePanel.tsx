import { useEffect, useRef, useState } from "react";
import { ApiError, post, type PreviewResponse } from "../api";
import { COUNTRY_NAMES } from "../lib/countries";

// 세부기술 탭에서 셀 하나를 고르고 "정밀 견적"을 누르면 열리는 패널. 대상은 항상
// 그 셀(세부기술 × 국가 × 연도)로 고정이므로 여기서 다시 고르게 하지 않는다.
//
// **실행 버튼은 없다.** 만드는 것은 세부기술 탭의 "선택한 N건 생성"
// (POST /admin/queue) 하나뿐이다 — 그쪽은 force와 건너뛴 사유를 모두 다루는데
// 여기서 또 실행할 수 있으면 같은 자리에 규약이 다른 경로가 둘 생긴다.
export default function EstimatePanel({
  adminKey,
  subfieldId,
  subfieldName,
  country,
  year,
  subfieldsVersion,
  onUnauthorized,
}: {
  adminKey: string;
  subfieldId: number;
  subfieldName: string;
  // 같은 세부기술·연도라도 국가가 다르면 다른 분석이다(analyses의 유일키에 country가 있다).
  country: string;
  year: number;
  // 세부기술 검색식이 바뀔 때마다(SubfieldEditor의 onChanged) Admin.tsx가 증가시키는 세대
  // 카운터. 이 화면 밖에서 값이 바뀌면 확인했던 숫자가 더 이상 유효하지 않다.
  subfieldsVersion: number;
  onUnauthorized: () => void;
}) {
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [staleNotice, setStaleNotice] = useState<string | null>(null);

  const handlePreview = async () => {
    setPreviewing(true);
    setError(null);
    setStaleNotice(null);
    try {
      setPreview(
        await post<PreviewResponse>(
          "/admin/preview",
          { subfield_id: subfieldId, year_from: year, year_to: year, country },
          adminKey,
        ),
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      setError(e instanceof Error ? e.message : "미리보기에 실패했습니다.");
    } finally {
      setPreviewing(false);
    }
  };

  // 대상이 이미 정해져 있으므로 여는 동작 자체가 조회를 시작한다 — 버튼 하나로 끝난다.
  useEffect(() => {
    handlePreview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 첫 렌더에서는 건너뛰고, 이후 세대 값이 바뀔 때만 폐기 + 사유 안내.
  const isFirstGen = useRef(true);
  useEffect(() => {
    if (isFirstGen.current) {
      isFirstGen.current = false;
      return;
    }
    setPreview(null);
    setError(null);
    setStaleNotice("검색식이 변경되어 견적을 다시 내야 합니다.");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subfieldsVersion]);

  return (
    <section className="mt-6 border border-border bg-surface p-4">
      <h2 className="text-lg font-semibold text-accent">정밀 견적</h2>
      <p className="mt-1 text-xs text-muted">
        검색만 수행합니다 —{" "}
        <span className="font-medium text-ink-light">
          LLM은 호출하지 않지만 OpenAlex 검색 비용(약 $0.002)이 소량 발생합니다.
        </span>{" "}
        실제 생성은 위의 &ldquo;생성&rdquo; 버튼으로 합니다.
      </p>

      {/* 대상을 이름으로 밝힌다 — 여기 없으면 화면 어디에도 안 남는다. */}
      <p className="mt-3 text-sm font-medium text-ink">
        {subfieldName} · {COUNTRY_NAMES[country] ?? country} · {year}년
      </p>

      {staleNotice && <p className="mt-3 text-sm text-warning">{staleNotice}</p>}
      {error && <p className="mt-3 text-sm text-danger">{error}</p>}
      {previewing && <p className="mt-3 text-sm text-muted">확인 중…</p>}

      {preview && (
        <div className="mt-4 border border-border-light bg-paper p-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <PreviewTile label="OpenAlex 전체 건수" value={preview.openalex_count.toLocaleString()} />
            {/* KCI는 한국학술지 전용이라 KR에서만 검색한다(search.collect와 같은 규약).
                타국에서 0을 그냥 보이면 "국내지가 안 잡혔다"로 오독된다 — 이유를 밝힌다. */}
            <PreviewTile
              label="KCI 표본 건수"
              value={
                country === "KR"
                  ? `${preview.kci_sample_count.toLocaleString()}${preview.kci_sample_truncated ? "+" : ""}`
                  : "—"
              }
              caption={
                country !== "KR"
                  ? "KCI는 한국학술지 전용 — 이 국가에서는 검색하지 않습니다"
                  : preview.kci_sample_truncated
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

          <div className="mt-3 banner banner-warn">
            <p className="text-xs text-ink-light">
              예상 총비용 <span className="font-medium">(추정치)</span>
            </p>
            <p className="mt-1 font-mono text-2xl font-semibold tabular-nums text-ink">
              ${preview.estimated_total_cost_usd.toFixed(4)}
            </p>
            <p className="mt-1 text-xs text-faint">
              OpenAlex ${preview.estimated_cost_usd.toFixed(4)} (실측 단가) + LLM(map) $
              {preview.estimated_llm_cost_usd.toFixed(4)} (논문당 평균 토큰 근사치 기반 추정 — 실제와
              다를 수 있음)
            </p>
          </div>

          <p className="mt-3 text-xs tabular-nums text-muted">
            OpenAlex 오늘 사용 ${preview.budget_spent.toFixed(4)} / ${preview.budget_limit.toFixed(2)}
          </p>

          {preview.over_limit && (
            <p className="mt-3 banner banner-risk">
              검색 결과가 처리 상한 {preview.max_papers.toLocaleString()}건을 초과합니다. 이대로
              생성하면 인용 상위 {preview.max_papers.toLocaleString()}건만 표본으로 분석되고 그
              사실이 통계에 남습니다. 전수로 보려면 검색식을 좁히거나 세부기술을 분할하세요.
            </p>
          )}

          {preview.samples.length > 0 && (
            <>
              <p className="mt-4 text-xs font-medium text-ink-light">표본 미리보기</p>
              <ul className="mt-1 space-y-1 text-xs text-muted">
                {preview.samples.slice(0, 5).map((s, i) => (
                  <li key={i}>
                    [{s.year ?? "연도 미상"}] {s.title}
                    {s.journal && <span className="text-faint"> · {s.journal}</span>}
                    {!s.has_abstract && <span className="ml-1 text-warning">(abstract 없음)</span>}
                  </li>
                ))}
              </ul>
            </>
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
      <p className="mt-1 text-lg tabular-nums text-ink">{value}</p>
      {caption && <p className="mt-1 text-xs text-faint">{caption}</p>}
    </div>
  );
}
