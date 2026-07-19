import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { get, type Analysis, type Reference } from "../api";
import TopBar from "../components/TopBar";
import Footer from "../components/Footer";
import StatusBadge from "../components/StatusBadge";
import CoverageBar from "../components/CoverageBar";
import StatsPanel from "../components/StatsPanel";

// h1(페이지 제목) > h2(섹션 제목) > h3(하위 제목) 위계를 report_md의 마크다운 헤딩(##, ###)에도
// 강제한다. prose 기본값에 맡기면 StatsPanel의 네이티브 h2/h3와 크기가 어긋난다.
const PROSE_HEADING_CLASSES =
  "prose-headings:font-display prose-headings:tracking-tight prose-headings:text-ink " +
  "prose-h2:text-xl prose-h2:font-bold prose-h2:mt-0 prose-h2:mb-4 " +
  "prose-h3:text-sm prose-h3:font-bold prose-h3:mt-8 prose-h3:mb-2";

function SectionDivider() {
  return <hr className="my-10 border-t border-border" />;
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
            서술을 읽기 전에 봐야 하는 전제이므로 섹션 순서와 무관하게 항상 최상단에 유지한다. */}
        <div className="avoid-break mt-4 max-w-sm border border-border bg-surface p-4">
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
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.report_md ?? ""}</ReactMarkdown>
          </div>

          <SectionDivider />

          <StatsPanel stats={data.stats} />

          <References references={data.references} />
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
        <p className="col-span-2">
          <span className="text-muted">모집단</span> 검색{" "}
          {data.searched_count.toLocaleString()}건 / 분석 대상 {data.analyzed_count.toLocaleString()}건
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
        <h2 className="font-display text-xl font-bold tracking-tight text-ink">참고문헌</h2>
        <ol className="mt-4 space-y-2 text-sm text-ink-light">
          {references.map((r) => (
            <li key={r.n} className="flex gap-2">
              <span className="shrink-0 font-mono text-xs text-muted">[{r.n}]</span>
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
