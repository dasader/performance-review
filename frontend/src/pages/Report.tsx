import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { get, type Analysis } from "../api";
import TopBar from "../components/TopBar";
import Footer from "../components/Footer";
import StatusBadge from "../components/StatusBadge";
import CoverageBar from "../components/CoverageBar";
import StatsPanel from "../components/StatsPanel";

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
      <header className="mb-8" style={{ animation: "fadeUp 0.3s ease-out both" }}>
        <p className="font-mono text-xs uppercase tracking-widest text-accent">
          {data.field_name}
        </p>
        <h1 className="mt-1 font-display text-3xl font-bold tracking-tight text-ink">
          {data.subfield_name} <span className="text-faint">{data.year}</span>
        </h1>

        <div className="mt-3">
          <StatusBadge status={data.status} label={data.status_label} />
        </div>

        {/* 검색 모집단과 분석 모집단이 다르다는 점을 감추지 않는다 */}
        {data.searched_count > 0 && (
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
        )}

        {data.snapshot_at && (
          <p className="mt-3 text-xs text-faint">
            수집 시점 {new Date(data.snapshot_at).toLocaleString("ko-KR")} 기준 (인용수 포함, 이후 변동 가능)
          </p>
        )}

        {data.status === "done" && (
          <button
            type="button"
            onClick={() => window.print()}
            className="mt-5 border border-ink px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-ink hover:text-paper print:hidden"
          >
            PDF로 저장
          </button>
        )}
      </header>

      {data.status !== "done" && <StatusPanel data={data} />}

      {data.status === "done" && (
        <>
          <StatsPanel stats={data.stats} />

          <div className="prose prose-neutral mt-10 max-w-none prose-headings:font-display prose-headings:tracking-tight prose-table:text-sm">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.report_md ?? ""}</ReactMarkdown>
          </div>
        </>
      )}
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
