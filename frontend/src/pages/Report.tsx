import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { type Components } from "react-markdown";
import {
  ACTIVE_STATUSES,
  get,
  getAvailability,
  type Analysis,
  type Availability,
  type Reference,
} from "../api";
import Switch from "../components/Switch";
import TopBar from "../components/TopBar";
import Footer from "../components/Footer";
import StatusBadge from "../components/StatusBadge";
import DoiLink from "../components/DoiLink";
import StatsPanel from "../components/StatsPanel";
import MetricTable from "../components/MetricTable";
import CountryBar from "../components/CountryBar";
import { firstCiteOffsets } from "../lib/citeAnchors";
import { useQueryFlag } from "../lib/hooks";
import { stripLeadingH1 } from "../lib/reportMarkdown";
import Prose from "../components/Prose";
import { MARKDOWN_COMPONENTS } from "../lib/prose";

// 연도 이동 링크가 국가를 잃으면 KR로 되돌아간다 — 다른 국가를 보다가 연도를 옮기면
// 조용히 다른 나라 보고서로 넘어간다.
function countryQuery(country: string): string {
  return country && country !== "KR" ? `?country=${encodeURIComponent(country)}` : "";
}

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

  return <Prose md={md} components={components} />;
}

export default function Report() {
  const { analysisId, subfieldId, year } = useParams();
  const [searchParams] = useSearchParams();
  // 같은 세부기술·연도라도 국가가 다르면 다른 분석이다. 쿼리로 실어 공유·북마크가
  // 되게 한다(기본 KR — 백엔드도 같은 기본값이라 붙이지 않아도 동작이 같다).
  // analysisId로 여는 경로는 행을 직접 가리키므로 국가가 필요 없다.
  const country = searchParams.get("country") ?? "KR";
  const [data, setData] = useState<Analysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 국가 줄은 본문과 무관한 이동 수단이라 실패해도 보고서 자체는 그대로 보여준다 —
  // catch에서 null로 두면 CountryBar가 그냥 안 그려질 뿐이다.
  const [avail, setAvail] = useState<Availability | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    const path = analysisId
      ? `/analyses/${analysisId}`
      : `/subfields/${subfieldId}/analyses/${year}?country=${encodeURIComponent(country)}`;
    get<Analysis>(path)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [analysisId, subfieldId, year, country]);

  useEffect(() => {
    // 세부기술을 옮기면(A→B) 이전 국가 줄이 한 프레임 남아 B의 보고서 위에
    // A의 국가·비교 목록이 잘못 뜬다(리뷰 지적) — 요청 전에 먼저 비운다.
    setAvail(null);
    if (!data) return;
    getAvailability(data.subfield_id, data.year)
      .then(setAvail)
      .catch(() => setAvail(null));
    // data 전체를 넣으면 응답이 바뀔 때마다(같은 subfield_id·year라도 참조가 새로
    // 만들어짐) 불필요하게 재요청한다 — 실제로 값이 바뀌는 두 필드만 본다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.subfield_id, data?.year]);

  return (
    <div className="min-h-screen">
      <TopBar />
      <article className="mx-auto max-w-4xl px-6 pb-10 pt-6">
        {error && <p className="text-sm text-danger">{error}</p>}
        {!data && !error && <p className="text-sm text-muted">불러오는 중…</p>}
        {data && <ReportBody data={data} avail={avail} />}
      </article>
      <Footer />
    </div>
  );
}

// 3단 reduce의 성과유형별 상세. 종합 보고서는 대표 성과 중심이라 개별 연구가 생략될 수
// 있어(실측: 논문 수와 무관하게 약 40편에서 포화), 유형별 중간 보고서를 함께 보관한다.
// 토글 상태를 URL 쿼리(?withSections=1)에 실어 공유·북마크가 되게 한다 —
// FieldReportPage의 withSub과 같은 패턴.
function SectionSummaries({ sections }: { sections?: { name: string; body_md: string }[] }) {
  const [open, toggle] = useQueryFlag("withSections");

  if (!sections?.length) return null;

  // 비교 화면과 같은 규칙 — 토글이 꺼져 있으면 인쇄에서 구획을 통째로 숨긴다.
  // 내용은 조건부라 안 나오지만 제목·설명·스위치가 남아 본문 없는 제목만 찍힌다.
  const printable = open ? "" : "print:hidden";

  return (
    <>
      <div className={printable}>
        <SectionDivider />
      </div>
      <section className={printable}>
        <div className="mb-2 flex flex-wrap items-center gap-4">
          <h2 className="text-xl font-bold tracking-tight text-accent">세부 보고서</h2>
          {/* 스위치는 조작물이라 인쇄에서는 언제나 뺀다. */}
          <span className="print:hidden">
            <Switch checked={open} onChange={toggle} label="성과유형별 상세 포함" />
          </span>
        </div>
        <p className="mb-4 text-sm text-muted">
          논문이 많아 성과유형별로 나눠 정리한 뒤 종합한 분석입니다. 종합 보고서는 대표
          성과 중심이라 개별 연구가 생략될 수 있어 유형별 상세를 함께 보관합니다. 각주
          번호는 위 본문과 같은 체계입니다.
        </p>
        {open &&
          sections.map((s, i) => (
            <article key={s.name} className="mt-10 break-before-page">
              {/* 그룹 이름표는 본문 제목과 **다른 종류의 물건**으로 보여야 한다. 이전에는
                  text-lg 제목이라 바로 아래 오는 prose-h2("분야 개괄", text-xl + 밑줄)보다
                  오히려 작아 위계가 뒤집혔고, 읽는 사람이 이게 기술명인지 절 제목인지
                  구분하지 못했다(사용자 신고). 눌린 면 + 왼쪽 굵은 띠로 "구획 표지"임을
                  드러내고, 눈썹 라벨이 그것이 성과유형임과 전체 중 몇 번째인지를 말한다. */}
              <div className="mb-4 border-l-4 border-accent bg-sunken px-4 py-3">
                <p className="text-eyebrow font-bold uppercase tracking-[0.09em] text-muted">
                  성과유형 {i + 1} / {sections.length}
                </p>
                <h3 className="text-xl font-bold text-ink">{s.name}</h3>
              </div>
              <ReportMarkdown md={stripLeadingH1(s.body_md)} />
            </article>
          ))}
      </section>
    </>
  );
}


function ReportBody({ data, avail }: { data: Analysis; avail: Availability | null }) {
  const excluded = data.searched_count - data.analyzed_count;
  // 이웃 연도는 실제로 분석 행이 있는 연도 중에서만 고른다 — year±1로 링크하면
  // 건너뛴 연도에서 404 화면이 뜬다.
  const prevYear = data.years.filter((y) => y < data.year).pop() ?? null;
  const nextYear = data.years.find((y) => y > data.year) ?? null;

  return (
    <>
      <PrintHeader data={data} />

      {/* 이동·출력 동작은 한 줄에 모은다 — 제목 왼쪽과 오른쪽으로 흩어져 있으면 시선이
          두 번 튄다(분야 보고서 화면과 같은 계약). 인쇄물에서는 통째로 숨긴다. */}
      <div className="mb-6 flex items-center justify-between gap-3 print:hidden">
        <Link to={`/fields/${data.field_id}`} className="btn btn-neutral btn-sm">
          ← {data.field_name} 화면으로
        </Link>
        <div className="flex items-center gap-2">
          {/* 연도 이동은 화살표만으로 두지 않고 갈 연도를 숫자로 밝힌다 — 연도가 띄엄띄엄
              있을 수 있어(2024 → 2026) "이전"이 몇 년인지 눌러 봐야 아는 상태가 된다.
              없는 방향은 비활성 버튼 대신 아예 그리지 않는다. */}
          {prevYear && (
            <Link
              to={`/subfields/${data.subfield_id}/${prevYear}${countryQuery(data.country)}`}
              className="btn btn-neutral btn-sm tabular-nums"
            >
              ← {prevYear}
            </Link>
          )}
          {nextYear && (
            <Link
              to={`/subfields/${data.subfield_id}/${nextYear}${countryQuery(data.country)}`}
              className="btn btn-neutral btn-sm tabular-nums"
            >
              {nextYear} →
            </Link>
          )}
          {data.status === "done" && (
            <button type="button" onClick={() => window.print()} className="btn btn-primary btn-sm">
              PDF로 저장
            </button>
          )}
        </div>
      </div>

      <header className="mb-6" style={{ animation: "fadeUp 0.3s ease-out both" }}>
        <p className="text-eyebrow font-bold uppercase tracking-[0.09em] text-muted">
          {data.field_name}
        </p>
        {/* 제목이 "무엇을 · 어느 나라 · 언제"를 한 줄로 말한다. 한국은 기준국이라
            넣지 않는다 — 대다수 화면에 늘 붙으면 그 표시가 신호 노릇을 못한다.
            국가·연도는 muted로 낮춰 세부기술명이 계속 주인공이게 둔다. */}
        <h1 className="mt-1 text-3xl font-extrabold tracking-tight text-ink">
          {data.subfield_name}{" "}
          {data.country !== "KR" && (
            <span className="text-muted">{data.country_name} </span>
          )}
          <span className="tabular-nums text-muted">{data.year}</span>
        </h1>
        {/* eyebrow는 한 덩어리에 하나만 쓴다 — 제목 위아래로 두 줄이 같은 대문자 라벨
            모양이면 어느 쪽이 상위 분류인지 읽히지 않는다. 아래는 평범한 캡션으로 둔다. */}
        <p className="mt-1 text-xs text-muted">
          분석 대상 기간 {data.year}년
        </p>

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

      </header>

      {avail && (
        <CountryBar
          subfieldId={data.subfield_id}
          year={data.year}
          current={data.country}
          countries={avail.countries}
          comparisons={avail.comparisons}
        />
      )}

      {data.status !== "done" && <StatusPanel data={data} />}

      {data.status === "done" && (
        <>
          {/* 국가 줄 바로 아래에는 구분선을 두지 않는다 — 줄 자체가 이미 헤더와
              본문을 가르므로 선을 더 그으면 두 겹으로 갈린다(사용자 지적). */}
          {/* 서술(주요 기술적 성과)을 통계보다 먼저 — 독자는 정책·기획 담당자이며 숫자보다
              무엇을 달성했는지를 먼저 읽는다. */}
          <ReportMarkdown md={stripLeadingH1(data.report_md ?? "")} />

          {/* 정량 지표 분포는 논문 간 통계(기관·저널·인용수)가 아니라 연구 내용 자체의
              결과값이다 — 서술을 읽은 직후 "그래서 어느 수준인가"를 잇는 자리가 맞아
              StatsPanel("기본 통계")이 아니라 본문 쪽에 둔다. */}
          {"top_metrics" in data.stats && (
            <>
              <SectionDivider />
              <MetricTable
                rows={data.stats.top_metrics ?? []}
                unique={data.stats.metrics_unique ?? 0}
                analysisId={data.id}
              />
            </>
          )}

          {/* 참고문헌은 그것을 인용한 본문 바로 다음이 자연스럽다 — 통계(기본 통계) 앞으로 옮긴다.
              References가 빈 배열이면 자신의 구분선 없이 null을 반환하므로, 그 경우에도
              아래 구분선 하나만 report_md와 StatsPanel 사이에 남는다. */}
          <References references={data.references} />

          <SectionSummaries sections={data.sections} />

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

// 목록이 길어(중국 보고서 147건) 기본은 접어 둔다. 세부 보고서 토글과 같은 규약으로
// URL 쿼리(?withRefs=1)에 실어 공유·북마크가 되게 한다.
//
// ★ 각주 [12]는 #ref-12로 점프한다. 접혀 있으면 대상이 DOM에 없어 링크가 죽으므로,
//   해시가 #ref-로 시작하면 자동으로 펼치고 한 프레임 뒤에 다시 스크롤한다.
//   (브라우저는 이미 스크롤을 시도했다가 실패한 상태다.)
// ★ 인쇄에는 접힘과 무관하게 항상 싣는다 — [12]가 있는데 참고문헌이 없는 PDF는
//   그 자체로 결함이다.
function References({ references }: { references: Reference[] }) {
  const [open, toggle] = useQueryFlag("withRefs");
  // 아래 해시 효과만 setSearchParams가 필요하다(펼침을 강제하는 쪽).
  const [, setSearchParams] = useSearchParams();

  useEffect(() => {
    const openIfTargeted = () => {
      if (!window.location.hash.startsWith("#ref-")) return;
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("withRefs", "1");
          return next;
        },
        { replace: true },
      );
      // 펼쳐진 뒤에야 대상이 레이아웃에 잡힌다.
      requestAnimationFrame(() =>
        document.getElementById(window.location.hash.slice(1))?.scrollIntoView(),
      );
    };
    openIfTargeted();
    window.addEventListener("hashchange", openIfTargeted);
    return () => window.removeEventListener("hashchange", openIfTargeted);
  }, [setSearchParams]);

  if (references.length === 0) return null;

  return (
    <>
      <SectionDivider />
      <section className="avoid-break">
        <div className="flex flex-wrap items-center gap-4">
          <h2 className="text-xl font-bold tracking-tight text-accent">
            참고문헌 <span className="text-faint">{references.length}</span>
          </h2>
          <span className="print:hidden">
            <Switch checked={open} onChange={toggle} label="목록 펼치기" />
          </span>
        </div>
        <ol
          className={`mt-4 space-y-2 text-sm text-ink-light print:block ${open ? "" : "hidden"}`}
        >
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
                    <DoiLink doi={r.doi} className="break-all">
                      doi.org/{r.doi}
                    </DoiLink>
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
