import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  get,
  type Field,
  type FieldReport,
  type RoadmapCheck,
  type SubfieldReportBody,
  type SubfieldReportsResponse,
} from "../api";
import TopBar from "../components/TopBar";
import Footer from "../components/Footer";
import Prose from "../components/Prose";
import ReferenceList from "../components/ReferenceList";
import { useQueryFlag } from "../lib/hooks";
import { formatGeneratedAt } from "../lib/format";
import { stripLeadingH1 } from "../lib/reportMarkdown";

// 분야 종합 보고서와 로드맵 이행 점검의 전용 페이지. 분야 화면(FieldDetail)의 접힌
// 섹션은 "있는지 훑어보는" 용도이고, 통째로 읽거나 PDF로 뽑는 건 여기서 한다 —
// 모달로 만들면 인쇄 시 뒤 페이지가 딸려 나오고 URL 공유도 안 된다.
//
// PDF는 브라우저 인쇄에 맡긴다(window.print + index.css의 @media print). 서버에서
// 굽는 것보다 한글 폰트·표 줄바꿈이 안전하고 의존성이 0이다 — Report.tsx와 같은 방식.
type Kind = "report" | "roadmap-check";

// 제목은 화면이 붙인다. 보고서 본문(report_md)은 제목 없이 "### 1. 점검 개요"부터
// 시작하도록 프롬프트에서 강제한다 — 둘 다 있으면 제목이 두 번 나온다.
function pageTitle(kind: Kind, fieldName: string): string {
  return kind === "report" ? `${fieldName} 분야 종합보고서` : `${fieldName} 분야 로드맵 점검결과`;
}

export default function FieldReportPage({ kind }: { kind: Kind }) {
  const { fieldId, year } = useParams();
  const [data, setData] = useState<FieldReport | RoadmapCheck | null>(null);
  const [fieldName, setFieldName] = useState("");
  const [error, setError] = useState<string | null>(null);

  // 세부기술 보고서 첨부 토글. URL 쿼리(?withSub=1)에 실어 상태를 공유·북마크 가능하게
  // 하고, 켜져 있으면 그대로 PDF로 저장하면 통짜로 출력된다. 분야 종합에만 있다 —
  // 로드맵 점검 결과에 세부기술 원문을 붙이는 건 성격이 안 맞는다.
  const [subFlag, toggleSub] = useQueryFlag("withSub");
  const withSub = kind === "report" && subFlag;
  const [subReports, setSubReports] = useState<SubfieldReportBody[] | null>(null);

  useEffect(() => {
    get<FieldReport | RoadmapCheck>(`/fields/${fieldId}/${kind}?year=${year}`)
      .then(setData)
      .catch((e) => setError(e.message));
    get<Field[]>("/fields")
      .then((all) => setFieldName(all.find((f) => f.id === Number(fieldId))?.name ?? ""))
      .catch(() => setFieldName(""));
  }, [fieldId, year, kind]);

  useEffect(() => {
    if (!withSub) {
      setSubReports(null);
      return;
    }
    let stale = false;
    get<SubfieldReportsResponse>(`/fields/${fieldId}/subfield-reports?year=${year}`)
      .then((r) => !stale && setSubReports(r.reports))
      .catch(() => !stale && setSubReports([]));
    return () => {
      stale = true;
    };
  }, [withSub, fieldId, year]);

  const check = kind === "roadmap-check" ? (data as RoadmapCheck | null) : null;

  return (
    <div className="min-h-screen">
      <TopBar />
      <main className="mx-auto max-w-4xl px-6 pb-10 pt-6">
        {/* 이동·출력 동작은 한 줄에 모은다 — 제목 왼쪽과 오른쪽으로 흩어져 있으면
            시선이 두 번 튄다. 인쇄물에서는 둘 다 의미가 없어 통째로 숨긴다. */}
        <div className="mb-6 flex items-center justify-between gap-3 print:hidden">
          <Link to={`/fields/${fieldId}`} className="btn btn-neutral btn-sm">
            ← {fieldName || "분야"} 화면으로
          </Link>
          <div className="flex items-center gap-2">
            {kind === "report" && data && (
              <button type="button" onClick={toggleSub} className="btn btn-neutral btn-sm">
                {withSub ? "세부기술 보고서 숨기기" : "세부기술 보고서 포함"}
              </button>
            )}
            {data && (
              <button type="button" onClick={() => window.print()} className="btn btn-primary btn-sm">
                PDF로 저장
              </button>
            )}
          </div>
        </div>

        {error && <p className="mt-4 text-sm text-danger">{error}</p>}
        {!data && !error && <p className="mt-4 text-sm text-muted">불러오는 중…</p>}

        {data && (
          <>
            <header>
              <p className="text-eyebrow font-bold uppercase tracking-[0.09em] text-muted">
                {kind === "report" ? "분야 종합 보고서" : "로드맵 이행 점검"}
              </p>
              <h1 className="mt-1 text-3xl font-bold tracking-tight text-ink">
                {pageTitle(kind, fieldName)} <span className="text-faint">{data.year}</span>
              </h1>

              <p className="mt-3 text-xs text-muted">
                세부기술 보고서 {data.source_count}건 기준
                {check && ` · 로드맵 ${check.roadmap_version} · 목표 ${check.goal_count}개`}
                {" · "}
                {formatGeneratedAt(data.generated_at)} 생성
              </p>

              {/* 전수 점검이 깨진 채 저장된 보고서는 "빠짐없이 봤다"로 읽히면 안 된다.
                  인쇄물에도 남겨야 한다 — 종이로 넘어가면 이 단서가 사라진다. */}
              {check?.incomplete && (
                <p className="mt-3 banner banner-risk">
                  로드맵 목표 {check.goal_count}개 중 {check.checked_count}개만 점검되었습니다.
                  일부 목표가 누락된 보고서입니다.
                </p>
              )}
              {data.stale && (
                <p className="mt-3 banner banner-warn">
                  생성 이후 원본이 변경되었습니다. 최신 상태가 아닐 수 있습니다.
                </p>
              )}

              {kind === "roadmap-check" && (
                <p className="mt-3 text-xs text-muted">
                  이 점검은 논문 성과만을 근거로 합니다 — 양산성·가격 경쟁력·자급률·실증처럼
                  논문에 실리지 않는 목표가 `데이터 없음`으로 표기된 것은 연구 부진을 뜻하지
                  않습니다.
                </p>
              )}
            </header>

            <hr className="my-10 border-t border-border" />

            <Prose md={stripLeadingH1(data.report_md)} />

            {/* 세부기술 보고서 첨부 — 종합보고서 뒤에 각 세부기술 원문을 이어붙인다.
                부록 배너는 종합보고서와 분리되게 새 페이지에서 시작하고(section의
                break-before-page), 첫 세부기술은 그 배너와 같은 페이지에 이어진다 —
                둘 다 break를 걸면 배너만 있는 빈 페이지가 생긴다. 둘째 세부기술부터만
                새 페이지에서 시작한다. 각 본문은 자체 H1을 걷어낸다(stripLeadingH1). */}
            {withSub && subReports && subReports.length > 0 && (
              <section className="mt-10 break-before-page">
                <p className="text-eyebrow font-bold uppercase tracking-[0.09em] text-muted">부록</p>
                <h2 className="mt-1 text-2xl font-bold tracking-tight text-ink">
                  세부기술별 상세 보고서
                </h2>
                <p className="mt-1 text-sm text-muted">
                  위 분야 종합보고서의 근거가 된 세부기술별 원문입니다.
                </p>
                {subReports.map((s, i) => (
                  <div key={s.name} className={i > 0 ? "break-before-page" : ""}>
                    {/* 세부기술명은 본문의 `##`(prose-h2: text-xl·accent)와 확실히
                        구분돼야 한다 — 큰 넘버 + 굵은 상단 이중선 + 검정 큰 글씨.
                        배경색이 아니라 테두리·넘버로 강조한다: 인쇄 시 배경색은 기본으로
                        빠지지만(브라우저 "배경 그래픽" 옵션) 테두리·텍스트는 항상 나온다. */}
                    <div className="mt-10 flex items-baseline gap-3 border-t-4 border-double border-ink pt-4">
                      <span className="text-2xl font-bold text-accent tabular-nums">
                        {String(i + 1).padStart(2, "0")}
                      </span>
                      <h3 className="text-2xl font-bold tracking-tight text-ink">
                        {s.name}
                      </h3>
                    </div>
                    <Prose className="mt-4" md={stripLeadingH1(s.report_md)} />
                    {/* 본문의 [n] 각주가 가리키는 참고문헌. 세부기술별로 번호가 새로
                        시작하므로 각 보고서 바로 아래에 둔다(여러 세부기술을 한 페이지에
                        올리면 #ref-n 앵커는 겹치지만, 인쇄물에서 필요한 건 번호-제목
                        대응이라 목록만 있으면 된다).
                        목록 양식은 세부기술 보고서와 같은 ReferenceList를 쓴다 — 예전에는
                        여기서 따로 조립해 글자 크기·구분자·DOI 링크가 전부 갈라져 있었다.
                        제목만 화면이 붙인다: 세부기술명(h3) 아래라 Report의 h2를 쓸 수 없어
                        본문 `###`(prose-h3)과 같은 크기로 맞춘다. */}
                    {s.references.length > 0 && (
                      <div className="avoid-break mt-6 border-t border-border pt-4">
                        <h4 className="text-base font-bold text-ink">
                          참고문헌 <span className="text-faint">{s.references.length}</span>
                        </h4>
                        <ReferenceList references={s.references} className="mt-2" />
                      </div>
                    )}
                  </div>
                ))}
              </section>
            )}
            {withSub && subReports?.length === 0 && (
              <p className="mt-6 text-sm text-muted">첨부할 완성된 세부기술 보고서가 없습니다.</p>
            )}
          </>
        )}
      </main>
      <Footer />
    </div>
  );
}
