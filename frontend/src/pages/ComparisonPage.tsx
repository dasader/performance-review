import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { getComparison, type Comparison } from "../api";
import TopBar from "../components/TopBar";
import Footer from "../components/Footer";
import Switch from "../components/Switch";
import { COUNTRY_NAMES, sortCountries } from "../lib/countries";
import { useQueryFlag, usePolling } from "../lib/hooks";
import Prose from "../components/Prose";
import { formatGeneratedAt } from "../lib/format";
import { stripLeadingH1 } from "../lib/reportMarkdown";

// 3개국 이상 비교의 쌍별(기준국 vs 각 상대국) 원본 보고서. 종합(report_md)은 국가를
// 가로질러 보이는 것만 다루므로, 개별 대조는 접어서 따로 보여준다. Report.tsx의
// SectionSummaries와 같은 규약(?withSections=1 + Switch) — 2개국 비교는 sections가
// 비어 있어(그 자체가 유일한 쌍) 아무것도 렌더링하지 않는다.
function PairwiseSections({ sections }: { sections?: { name: string; body: string }[] }) {
  const [open, toggle] = useQueryFlag("withSections");

  if (!sections?.length) return null;

  // 토글이 꺼져 있으면 인쇄에서 이 구획을 통째로 숨긴다. 내용은 이미 조건부라
  // 안 나오지만 제목·설명·스위치가 남아, PDF에 본문 없는 제목만 찍혔다(사용자 지적).
  const printable = open ? "" : "print:hidden";

  return (
    <>
      <hr className={`my-10 border-t border-border ${printable}`} />
      <section className={printable}>
        <div className="mb-2 flex flex-wrap items-center gap-4">
          <h2 className="text-xl font-bold tracking-tight text-accent">국가 간 비교</h2>
          {/* 스위치는 조작물이라 인쇄에서는 언제나 뺀다 — 종이에서 누를 수 없다. */}
          <span className="print:hidden">
            <Switch checked={open} onChange={toggle} label="국가별 1:1 대조 포함" />
          </span>
        </div>
        <p className="mb-4 text-sm text-muted">
          국가가 셋 이상이면 한국과 각 나라를 1:1로 대조한 뒤 종합합니다. 위 종합은 국가를
          가로질러 보이는 것만 다루므로, 개별 대조는 여기서 봅니다.
        </p>
        {open &&
          sections.map((s, i) => (
            <article key={s.name} className="mt-10 break-before-page">
              <div className="mb-4 border-l-4 border-accent bg-sunken px-4 py-3">
                <p className="text-eyebrow font-bold uppercase tracking-[0.09em] text-muted">
                  1:1 비교 {i + 1} / {sections.length}
                </p>
                <h3 className="text-xl font-bold text-ink">{s.name}</h3>
              </div>
              <Prose md={stripLeadingH1(s.body)} />
            </article>
          ))}
      </section>
    </>
  );
}

// 국가 비교 보고서 전용 페이지. 분야 보고서(FieldReportPage)와 같은 규약 —
// 생성은 큐잉이라 pending이면 폴링하고, PDF는 브라우저 인쇄에 맡긴다.
//
// 국가를 라우트 세그먼트가 아니라 쿼리스트링으로 받는 이유: 조합이 자유로워
// (KR,US / KR,US,CN / …) 경로로 두면 같은 화면이 국가 수만큼 갈라진다.
export default function ComparisonPage() {
  const { subfieldId, year } = useParams();
  const [searchParams] = useSearchParams();
  const [data, setData] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);

  const countriesParam = searchParams.get("countries") ?? "";
  const countries = countriesParam.split(",").filter(Boolean);

  const load = useCallback(
    () =>
      getComparison(Number(subfieldId), Number(year), countries)
        .then((d) => {
          setData(d);
          setError(null);
        })
        .catch((e) => setError(e.message)),
    // countries는 매 렌더 새 배열이라 의존성에 넣으면 무한 루프가 된다 —
    // 원본 문자열을 쓴다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [subfieldId, year, countriesParam],
  );

  useEffect(() => {
    if (countries.length < 2) return;
    load();
  }, [load, countries.length]);

  // pending이면 폴링한다. 생성은 잡 루프가 한 틱(30초)에 하나씩 처리하므로
  // 즉시 끝나지 않는다 — 분야 보고서와 같은 이유.
  usePolling(data?.status === "pending", load);

  return (
    <div className="min-h-screen">
      <TopBar />
      <main className="mx-auto max-w-4xl px-6 pb-10 pt-6">
        <div className="mb-6 flex items-center justify-between gap-3 print:hidden">
          <Link to={`/subfields/${subfieldId}/${year}`} className="btn btn-neutral btn-sm">
            ← 세부기술 보고서로
          </Link>
          {data?.report_md && (
            <button
              type="button"
              onClick={() => window.print()}
              className="btn btn-primary btn-sm"
            >
              PDF로 저장
            </button>
          )}
        </div>

        {countries.length < 2 && (
          <p className="banner banner-risk">비교하려면 국가가 2개 이상이어야 합니다.</p>
        )}
        {error && <p className="mt-4 text-sm text-danger">{error}</p>}
        {!data && !error && countries.length >= 2 && (
          <p className="mt-4 text-sm text-muted">불러오는 중…</p>
        )}

        {data && (
          <>
            <header>
              <p className="text-eyebrow font-bold uppercase tracking-[0.09em] text-muted">
                국가 비교 보고서
              </p>
              {/* 세부기술 보고서와 같은 규칙 — "무엇을 · 어느 나라 · 언제". 어느
                  나라들을 보고 있는지가 제목에서 바로 읽혀야 한다. */}
              <h1 className="mt-1 text-3xl font-bold tracking-tight text-ink">
                {data.subfield_name}{" "}
                <span className="text-muted">{sortCountries(data.countries).map((c) => COUNTRY_NAMES[c] ?? c).join(" · ")} </span>
                <span className="tabular-nums text-muted">{data.year}</span>
              </h1>

              <p className="mt-3 text-xs text-muted">
                {data.source_count}개국 기준{" · "}
                {formatGeneratedAt(data.generated_at)} 생성
              </p>

              {/* 처음 생성 중이면 report_md가 비어 있고, 재생성 중이면 이전 본문이
                  담겨 있다 — 후자는 옛 보고서를 보여주며 폴링하므로 문구가 다르다. */}
              {data.status === "pending" && (
                <p className="mt-3 banner banner-warn">
                  {data.report_md
                    ? "재생성 중입니다. 아래는 이전 보고서이며, 완료되면 자동으로 갱신됩니다."
                    : "비교 보고서를 생성하고 있습니다. 완료되면 자동으로 갱신됩니다."}
                </p>
              )}
              {data.status === "failed" && (
                <p className="mt-3 banner banner-risk">생성 실패: {data.error}</p>
              )}

              {/* 표본율·결측률이 국가마다 다르다는 점은 보고서 본문의 "이 비교의 한계"가
                  다루지만, 화면에서도 한 줄로 밝힌다 — 본문을 끝까지 읽지 않고 앞의
                  숫자만 보고 판단하는 경우가 있다. */}
              <p className="mt-3 text-xs text-muted">
                영문 국제학술지 기준이라 각국 자국어 학술지(중국 CNKI 등)는 빠져 있습니다.
                수집 건수가 상한에 걸린 국가는 인용 상위 논문만 반영됩니다.
              </p>
            </header>

            <hr className="my-10 border-t border-border" />

            {data.report_md && (
              <Prose md={stripLeadingH1(data.report_md)} />
            )}

            <PairwiseSections sections={data.sections} />
          </>
        )}
      </main>
      <Footer />
    </div>
  );
}
