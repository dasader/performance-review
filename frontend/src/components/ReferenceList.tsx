import type { Reference } from "../api";
import DoiLink from "./DoiLink";

// 각주 점프 대상(각주 [n] ↔ 참고문헌 항목)이 화면 맨 위에 딱 붙지 않게 여유를 둔다.
// 양쪽 끝(cite-n 앵커는 Report.tsx)이 같은 값을 써야 왕복이 같은 여백으로 멈춘다.
export const SCROLL_TARGET_CLASS = "scroll-mt-20";

// 참고문헌 목록 본체. 세부기술 보고서(Report)와 분야 보고서 부록(FieldReportPage)이
// 같은 Reference[]를 각자 그리다 양식이 갈라져 있었다 — 글자 크기(text-sm/text-xs),
// 저널·연도 구분자(`— journal, year` / `· journal (year)`), DOI 링크 유무가 전부 달랐다.
// 목록만 여기 모으고 제목·펼침 스위치는 화면이 각자 붙인다(부록은 세부기술 제목 아래에
// 들어가 Report와 같은 위계의 h2를 쓸 수 없다).
//
// backlink: 본문의 [n]으로 되돌아가는 ↑ 링크. 부록에서는 끈다 — 거기 본문에는
// #cite-n 앵커가 없고(각주 렌더러를 얹지 않는다), 여러 세부기술이 한 페이지에 있어
// 번호도 겹친다. 켜면 죽은 링크만 늘어난다.
export default function ReferenceList({
  references,
  backlink = false,
  className = "",
}: {
  references: Reference[];
  backlink?: boolean;
  className?: string;
}) {
  return (
    <ol className={`space-y-2 text-sm text-ink-light${className ? ` ${className}` : ""}`}>
      {references.map((r) => (
        <li key={r.n} id={`ref-${r.n}`} className={`flex gap-2 ${SCROLL_TARGET_CLASS}`}>
          <span className="shrink-0 font-mono text-xs text-muted">
            [{r.n}]
            {backlink && (
              <>
                {" "}
                <a
                  href={`#cite-${r.n}`}
                  aria-label={`본문 ${r.n}번 인용 위치로 이동`}
                  className="text-accent no-underline hover:underline print:hidden"
                >
                  ↑
                </a>
              </>
            )}
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
  );
}
