import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkCjkFriendly from "remark-cjk-friendly";
import { ACTIVE_STATUSES, get, type Analysis, type Reference } from "../api";
import TopBar from "../components/TopBar";
import Footer from "../components/Footer";
import StatusBadge from "../components/StatusBadge";
import CoverageBar from "../components/CoverageBar";
import StatsPanel from "../components/StatsPanel";
import { firstCiteOffsets } from "../lib/citeAnchors";
import { stripLeadingH1 } from "../lib/reportMarkdown";
import { MARKDOWN_COMPONENTS, PROSE_CLASSES } from "../lib/prose";

function SectionDivider() {
  return <hr className="my-10 border-t border-border" />;
}

// scroll-mt-20 — 점프 대상(각주 ↔ 참고문헌)이 화면 맨 위에 딱 붙지 않게 여유를 둔다.
const SCROLL_TARGET_CLASS = "scroll-mt-20";

// report_md 각주 링크([\[1\]](#ref-1))에 "본문으로 돌아가기"용 id를 붙인다. 같은 논문이 여러
// 번 인용될 수 있어 백엔드는 몇 번째가 "첫 인용"인지 추적하지 않으므로, md에서 그 번호가 처음
// 나오는 offset을 미리 구해 두고 렌더 중 노드 위치와 대조한다. 원문에 앵커 태그를 심고
// rehype-raw로 파싱하는 것보다 단순하다.
function ReportMarkdown({ md }: { md: string }) {
  const components: Components = useMemo(() => {
    const first = firstCiteOffsets(md);
    return {
      ...MARKDOWN_COMPONENTS,
      a({ href, children, node, ...props }) {
        const refN = href?.startsWith("#ref-") ? href.slice("#ref-".length) : null;
        if (refN && node?.position?.start.offset === first.get(refN)) {
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
  }, [md]);

  // remarkCjkFriendly는 remarkGfm 뒤에 온다 — 패키지 README의 권장 순서(parse -> gfm ->
  // cjk-friendly -> rehype)를 따른다. CommonMark는 닫는 `**` 바로 뒤에 공백 없이 한글
  // 등 CJK 글자가 붙으면 강조로 인식하지 않는데(한국어는 조사를 띄어쓰지 않으므로 report_md에서
  // 구조적으로 계속 발생), 이 플러그인이 그 경우를 강조로 인식하도록 판정을 완화한다.
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkCjkFriendly]} components={components}>
      {md}
    </ReactMarkdown>
  );
}

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
      <article className="mx-auto max-w-4xl px-6 pb-10 pt-6">
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

      <header className="mb-6" style={{ animation: "fadeUp 0.3s ease-out both" }}>
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-eyebrow font-bold uppercase tracking-[0.09em] text-muted">
              {data.field_name}
            </p>
            <h1 className="mt-1 text-3xl font-extrabold tracking-tight text-ink">
              {data.subfield_name} <span className="tabular-nums text-muted">{data.year}</span>
            </h1>
            {/* eyebrow는 한 덩어리에 하나만 쓴다 — 제목 위아래로 두 줄이 같은 대문자 라벨
                모양이면 어느 쪽이 상위 분류인지 읽히지 않는다. 아래는 평범한 캡션으로 둔다. */}
            <p className="mt-1 text-xs text-muted">분석 대상 기간 {data.year}년</p>
          </div>

          {data.status === "done" && (
            <button
              type="button"
              onClick={() => window.print()}
              className="shrink-0 btn btn-primary print:hidden"
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
            print:hidden — PDF에서는 이 격자가 PrintHeader와 중복된다. 검색/분석 건수 정보
            자체는 PDF에서 완전히 사라지면 안 되므로, 그 역할은 아래 StatsPanel("기본 통계")의
            검색 논문/분석 대상 타일(print:hidden 없음)이 대신한다.

            문서 메타 격자 — 값이 헤드라인 수치가 아니라 참조 정보(모집단·제외·수집 시점)라
            칸이 얕고 값이 작다. 통계 타일과 같은 뼈대(1px 괘선 틈)를 쓰고 크기만 다르다:
            화면마다 비슷한 격자를 로컬 CSS로 다시 짜면 padding과 값 크기가 조용히 어긋난다.
            제외 0건은 빈칸이 아니라 —로 쓴다 — 0과 "값 없음"은 다른 정보다. */}
        <div className="avoid-break mt-4 grid grid-cols-2 gap-px border border-border bg-border sm:grid-cols-4 print:hidden">
          <MetaCell label="검색 논문" value={`${data.searched_count.toLocaleString()}건`} />
          <MetaCell label="분석 대상" value={`${data.analyzed_count.toLocaleString()}건`} />
          <MetaCell
            label="제외"
            value={excluded > 0 ? `${excluded.toLocaleString()}건` : "—"}
            note="abstract 미보유 등"
          />
          <MetaCell
            label="수집 시점"
            value={
              data.snapshot_at ? new Date(data.snapshot_at).toLocaleDateString("ko-KR") : "—"
            }
            note="인용수 포함 · 이후 변동 가능"
          />
        </div>

        {/* 분석 대상 비율은 스케일이 있는 값이라 칩이 아니라 게이지로 말한다.
            확인 채널은 하나다 — 같은 것을 칩과 게이지로 두 번 말하지 않는다. */}
        <div className="mt-3 max-w-sm print:hidden">
          <CoverageBar searched={data.searched_count} analyzed={data.analyzed_count} />
        </div>
      </header>

      {data.status !== "done" && <StatusPanel data={data} />}

      {data.status === "done" && (
        <>
          <SectionDivider />

          {/* 서술(주요 기술적 성과)을 통계보다 먼저 — 독자는 정책·기획 담당자이며 숫자보다
              무엇을 달성했는지를 먼저 읽는다. */}
          <div className={`report-prose ${PROSE_CLASSES}`}>
            <ReportMarkdown md={stripLeadingH1(data.report_md ?? "")} />
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

// 문서 메타 격자의 한 칸. 라벨은 eyebrow(11px), 값은 본문 크기 — 헤드라인 수치가 아니다.
function MetaCell({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="bg-surface p-3">
      <p className="text-eyebrow font-bold uppercase tracking-[0.09em] text-muted">{label}</p>
      <p className="mt-1 text-sm font-semibold tabular-nums text-ink">{value}</p>
      {note && <p className="mt-1 text-eyebrow text-muted">{note}</p>}
    </div>
  );
}

// 인쇄(PDF 저장) 전용 문서 헤더 — 화면에서는 보이지 않고 @media print에서만 나타난다.
// 매 페이지 반복 헤더(@page)는 브라우저 지원이 제한적이라, 첫 페이지 상단에 한 번
// 나오는 블록으로 대신한다.
function PrintHeader({ data }: { data: Analysis }) {
  return (
    <div className="mb-6 hidden border-b border-ink pb-4 print:block">
      <p className="text-sm font-bold tracking-tight text-ink">전략기술 논문성과 분석</p>
      <p className="mt-1 break-all font-mono text-eyebrow text-muted">{window.location.href}</p>
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
            : "—"}
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
        <h2 className="text-xl font-bold tracking-tight text-accent">참고문헌</h2>
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
                      className="break-all text-ink underline decoration-border-strong underline-offset-2 hover:decoration-ink"
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
      <div className="banner banner-risk">
        <p className="text-sm font-medium text-danger">분석이 실패했습니다.</p>
        {data.error && <p className="mt-2 whitespace-pre-wrap text-xs text-ink-light">{data.error}</p>}
      </div>
    );
  }

  if (data.status === "paused") {
    return (
      <div className="banner banner-warn">
        <p className="text-sm font-medium text-warning">예산 소진으로 일시중지되었습니다.</p>
        <p className="mt-2 text-xs text-ink-light">
          할당된 분석 예산을 모두 사용해 처리가 중단된 상태입니다. 예산이 보충되면 이어서 진행됩니다.
        </p>
      </div>
    );
  }

  if (ACTIVE_STATUSES.has(data.status)) {
    return (
      <div className="border border-border bg-surface p-4">
        <p className="text-sm font-medium text-ink">보고서를 준비하는 중입니다 — {data.status_label}</p>
        <progress className="mt-3" aria-label="분석 진행 중" />
        <p className="mt-3 text-xs text-muted">
          잠시 후 새로고침하면 진행 상황을 다시 확인할 수 있습니다.
        </p>
        <button
          type="button"
          onClick={() => location.reload()}
          className="mt-3 btn btn-neutral btn-sm"
        >
          새로고침
        </button>
      </div>
    );
  }

  return null;
}
