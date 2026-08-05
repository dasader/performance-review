// DOI 바깥 링크. 세 화면(참고문헌·기본 통계·지표 드릴다운)이 같은 링크를 각자
// 조립하다 이미 갈라져 있었다 — rel이 "noreferrer"/"noopener noreferrer"로 나뉘고,
// 밑줄 색도 decoration-border/decoration-border-strong 두 종이 됐다. 밑줄 색은
// prose 본문 링크(lib/prose.tsx의 prose-a:*)와 같은 값으로 맞춘다: 같은 페이지
// 안에서 본문의 링크와 표의 링크가 다른 회색을 쓸 이유가 없다.
export default function DoiLink({
  doi,
  className = "",
  children,
}: {
  doi: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <a
      href={`https://doi.org/${doi}`}
      target="_blank"
      rel="noreferrer"
      className={`text-ink underline decoration-border-strong underline-offset-2 hover:decoration-ink${
        className ? ` ${className}` : ""
      }`}
    >
      {children}
    </a>
  );
}
