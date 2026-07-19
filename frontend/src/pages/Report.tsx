import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { get, type Analysis, type Reference } from "../api";
import TopBar from "../components/TopBar";
import Footer from "../components/Footer";
import StatusBadge from "../components/StatusBadge";
import CoverageBar from "../components/CoverageBar";
import StatsPanel from "../components/StatsPanel";

// h1(페이지 제목) > h2(섹션 제목 — "주요 기술적 성과"/"기본 통계" 등) > h3(하위 제목) > 본문
// 위계를 report_md의 마크다운 헤딩(##, ###)에도 강제한다. prose 기본값에 맡기면 h3가
// text-sm(본문 text-base보다 작음)으로 떨어져 위계가 역전된다 — 마크다운 헤딩은 항상
// 본문보다 커야 하고, 같은 레벨은 항상 같은 크기여야 한다. prose-h2는 StatsPanel/References의
// 네이티브 h2("기본 통계", "참고문헌")와 같은 text-xl로 맞춰 "섹션 제목" 레벨을 통일한다.
// [&>*:first-child]:mt-0 은 (구체적 헤딩 레벨과 무관하게) 블록의 첫 자식 위쪽 여백만 지워
// SectionDivider 바로 아래가 뜨지 않게 하면서, 두 번째 이후 헤딩(예: "## 주제 클러스터")의
// 여백은 살려 섹션 경계가 보이게 한다.
//
// h2("섹션 제목" 레벨)만 accent 색을 준다 — 모든 레벨에 색을 주면 구분이 사라지므로 h3/본문은
// prose-headings의 기본 text-ink를 그대로 물려받는다. 크기·굵기 차이는 그대로 유지되므로 색맹
// 등 색만으로 위계를 못 읽는 경우에도 구분 가능하다(접근성). prose-a는 각주 링크([1] → 참고문헌)
// 색을 References의 DOI 링크와 통일한다.
const PROSE_HEADING_CLASSES =
  "prose-headings:font-display prose-headings:tracking-tight prose-headings:text-ink " +
  "[&>*:first-child]:mt-0 " +
  "prose-h2:text-xl prose-h2:font-bold prose-h2:mt-12 prose-h2:mb-4 prose-h2:text-accent " +
  "prose-h3:text-lg prose-h3:font-bold prose-h3:mt-8 prose-h3:mb-2 " +
  "prose-a:text-accent prose-a:underline prose-a:decoration-border prose-a:underline-offset-2 hover:prose-a:decoration-accent";

function SectionDivider() {
  return <hr className="my-10 border-t border-border" />;
}

// scroll-mt-20 — 점프 대상(각주 ↔ 참고문헌)이 화면 맨 위에 딱 붙지 않게 여유를 둔다.
const SCROLL_TARGET_CLASS = "scroll-mt-20";

// report_md 각주 링크([\[1\]](#ref-1))에 "본문으로 돌아가기"용 id를 붙인다. 같은 논문이 여러
// 번 인용될 수 있어 백엔드는 몇 번째가 "첫 인용"인지 추적하지 않는다 — 여기 프론트는 이미
// 링크를 하나씩 순서대로 렌더링하고 있으므로, seenRefs Set으로 #ref-n을 처음 만난 시점만
// 표시하면 된다. 원문에 앵커 태그를 심고 rehype-raw로 파싱하는 것보다 훨씬 단순하다.
function ReportMarkdown({ md }: { md: string }) {
  const seenRefs = useRef(new Set<string>());

  const components: Components = {
    a({ href, children, ...props }) {
      const refN = href?.startsWith("#ref-") ? href.slice("#ref-".length) : null;
      if (refN && !seenRefs.current.has(refN)) {
        seenRefs.current.add(refN);
        return (
          <a href={href} id={`cite-${refN}`} className={SCROLL_TARGET_CLASS} {...props}>
            {children}
          </a>
        );
      }
      return (
        <a href={href} {...props}>
          {children}
        </a>
      );
    },
  };

  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {md}
    </ReactMarkdown>
  );
}

const IN_PROGRESS = new Set(["pending", "searching", "extracting", "reducing"]);

export default function Report() {
  const { analysisId, subfieldId, year } = useParams();
  const [data, setData] = useState<Analysis | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    const path = analysisId ? `/analyses/${analysisId}` : `/subfields/${subfieldId}/analyses/${year}`;
    get<Analysis>(path)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [analysisId, subfieldId, year]);

  return (
    <div className="min-h-screen">
      <TopBar />
      <article className="mx-auto max-w-4xl px-6 py-12">
        {error && <p className="text-sm text-danger">{error}</p>}
        {!data && !error && <p className="text-sm text-muted">불러오는 중…</p>}
        {data && <ReportBody data={data} />}
      </article>
      <Footer />
    </div>
  );
}

function ReportBody({ data }: { data: Analysis }) {
  const excluded = data.searched_count - data.analyzed_count;

  return (
    <>
      <PrintHeader data={data} />

      <header className="mb-8" style={{ animation: "fadeUp 0.3s ease-out both" }}>
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="font-mono text-xs uppercase tracking-widest text-accent">
              {data.field_name}
            </p>
            <h1 className="mt-1 font-display text-3xl font-bold tracking-tight text-ink">
              {data.subfield_name} <span className="text-faint">{data.year}</span>
            </h1>
            <p className="mt-1 font-mono text-xs uppercase tracking-widest text-muted">
              분석 대상 기간 {data.year}년
            </p>
          </div>

          {data.status === "done" && (
            <button
              type="button"
              onClick={() => window.print()}
              className="shrink-0 border border-ink px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-ink hover:text-paper print:hidden"
            >
              PDF로 저장
            </button>
          )}
        </div>

        <div className="mt-3">
          <StatusBadge status={data.status} label={data.status_label} />
        </div>

        {/* 검색 모집단과 분석 모집단이 다르다는 점을 감추지 않는다. 0건도 정보이므로 항상 표시한다.
            서술을 읽기 전에 봐야 하는 전제이므로 섹션 순서와 무관하게 항상 최상단에 유지한다.
            print:hidden — PDF에서는 이 박스가 PrintHeader와 중복된다. 검색/분석 건수 정보
            자체는 PDF에서 완전히 사라지면 안 되므로, 그 역할은 아래 StatsPanel("기본 통계")의
            검색 논문/분석 대상 타일(print:hidden 없음)이 대신한다. */}
        <div className="avoid-break mt-4 max-w-sm border border-border bg-surface p-4 print:hidden">
          <p className="text-sm text-ink-light">
            검색 <span className="font-mono tabular-nums">{data.searched_count.toLocaleString()}</span>
            건 / 분석 대상{" "}
            <span className="font-mono tabular-nums">{data.analyzed_count.toLocaleString()}</span>건
          </p>
          <div className="mt-2">
            <CoverageBar searched={data.searched_count} analyzed={data.analyzed_count} />
          </div>
          {excluded > 0 && (
            <p className="mt-2 text-xs text-muted">abstract 미보유 등 사유로 {excluded.toLocaleString()}건 제외</p>
          )}
          {data.sampled && (
            <p className="mt-1 text-xs text-muted">성과 서술은 표본 기준, 통계는 전수 기준입니다.</p>
          )}
        </div>

        {data.snapshot_at && (
          <p className="mt-3 text-xs text-faint">
            수집 시점 {new Date(data.snapshot_at).toLocaleString("ko-KR")} 기준 (인용수 포함, 이후 변동 가능)
          </p>
        )}
      </header>

      {data.status !== "done" && <StatusPanel data={data} />}

      {data.status === "done" && (
        <>
          <SectionDivider />

          {/* 서술(주요 기술적 성과)을 통계보다 먼저 — 독자는 정책·기획 담당자이며 숫자보다
              무엇을 달성했는지를 먼저 읽는다. */}
          <div className={`prose prose-neutral max-w-none prose-table:text-sm ${PROSE_HEADING_CLASSES}`}>
            <ReportMarkdown md={data.report_md ?? ""} />
          </div>

          {/* 참고문헌은 그것을 인용한 본문 바로 다음이 자연스럽다 — 통계(기본 통계) 앞으로 옮긴다.
              References가 빈 배열이면 자신의 구분선 없이 null을 반환하므로, 그 경우에도
              아래 구분선 하나만 report_md와 StatsPanel 사이에 남는다. */}
          <References references={data.references} />

          <SectionDivider />

          <StatsPanel stats={data.stats} />
        </>
      )}
    </>
  );
}

// 인쇄(PDF 저장) 전용 문서 헤더 — 화면에서는 보이지 않고 @media print에서만 나타난다.
// 매 페이지 반복 헤더(@page)는 브라우저 지원이 제한적이라, 첫 페이지 상단에 한 번
// 나오는 블록으로 대신한다.
function PrintHeader({ data }: { data: Analysis }) {
  return (
    <div className="mb-8 hidden border-b border-ink pb-4 print:block">
      <p className="font-display text-sm font-bold tracking-tight text-ink">전략기술 논문성과 분석</p>
      <p className="mt-0.5 break-all font-mono text-[11px] text-muted">{window.location.href}</p>
      {/* 검색/분석 건수 줄은 의도적으로 뺀다 — "기본 통계" 섹션의 검색 논문/분석 대상
          타일이 인쇄물에서 같은 정보를 이미 전달하므로, 여기서 중복 표시하지 않는다. */}
      <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-ink-light">
        <p><span className="text-muted">분야</span> {data.field_name}</p>
        <p><span className="text-muted">세부기술</span> {data.subfield_name}</p>
        <p><span className="text-muted">분석 대상 기간</span> {data.year}년</p>
        <p>
          <span className="text-muted">분석 시점</span>{" "}
          {data.snapshot_at
            ? `${new Date(data.snapshot_at).toLocaleString("ko-KR")} 기준 (인용수 포함)`
            : "-"}
        </p>
      </div>
    </div>
  );
}

function References({ references }: { references: Reference[] }) {
  if (references.length === 0) return null;

  return (
    <>
      <SectionDivider />
      <section className="avoid-break">
        <h2 className="font-display text-xl font-bold tracking-tight text-accent">참고문헌</h2>
        <ol className="mt-4 space-y-2 text-sm text-ink-light">
          {references.map((r) => (
            <li key={r.n} id={`ref-${r.n}`} className={`flex gap-2 ${SCROLL_TARGET_CLASS}`}>
              <span className="shrink-0 font-mono text-xs text-muted">
                [{r.n}]{" "}
                <a
                  href={`#cite-${r.n}`}
                  aria-label={`본문 ${r.n}번 인용 위치로 이동`}
                  className="text-accent no-underline hover:underline print:hidden"
                >
                  ↑
                </a>
              </span>
              <span>
                {r.title}
                {(r.journal || r.year) && (
                  <span className="text-muted"> — {[r.journal, r.year].filter(Boolean).join(", ")}</span>
                )}
                {r.doi && (
                  <>
                    {" "}
                    <a
                      href={`https://doi.org/${r.doi}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-accent underline decoration-border underline-offset-2 hover:decoration-accent"
                    >
                      doi.org/{r.doi}
                    </a>
                  </>
                )}
              </span>
            </li>
          ))}
        </ol>
      </section>
    </>
  );
}

function StatusPanel({ data }: { data: Analysis }) {
  if (data.status === "failed") {
    return (
      <div className="border border-danger/40 bg-danger/5 p-5">
        <p className="text-sm font-medium text-danger">분석이 실패했습니다.</p>
        {data.error && <p className="mt-2 whitespace-pre-wrap text-xs text-ink-light">{data.error}</p>}
      </div>
    );
  }

  if (data.status === "paused") {
    return (
      <div className="border border-warning/40 bg-warning/5 p-5">
        <p className="text-sm font-medium text-warning">예산 소진으로 일시중지되었습니다.</p>
        <p className="mt-2 text-xs text-ink-light">
          할당된 분석 예산을 모두 사용해 처리가 중단된 상태입니다. 예산이 보충되면 이어서 진행됩니다.
        </p>
      </div>
    );
  }

  if (IN_PROGRESS.has(data.status)) {
    return (
      <div className="border border-border bg-surface p-5">
        <p className="text-sm font-medium text-ink">보고서를 준비하는 중입니다 — {data.status_label}</p>
        <progress className="mt-3" aria-label="분석 진행 중" />
        <p className="mt-3 text-xs text-muted">
          잠시 후 새로고침하면 진행 상황을 다시 확인할 수 있습니다.
        </p>
        <button
          type="button"
          onClick={() => location.reload()}
          className="mt-3 border border-border px-3 py-1.5 text-xs text-ink-light hover:border-accent hover:text-accent"
        >
          새로고침
        </button>
      </div>
    );
  }

  return null;
}
