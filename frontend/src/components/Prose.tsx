import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkCjkFriendly from "remark-cjk-friendly";
import { MARKDOWN_COMPONENTS, PROSE_CLASSES } from "../lib/prose";

// 보고서 본문 한 덩어리 — 래퍼 클래스 + remark 플러그인 조합.
//
// 세 화면 다섯 곳이 이 두 줄을 각자 적고 있었다. 줄 수가 아니라 **플러그인 순서가
// 계약**이라는 점이 문제다: remarkCjkFriendly는 remarkGfm 뒤에 와야 한다(패키지
// README의 권장 순서 parse -> gfm -> cjk-friendly -> rehype). CommonMark는 닫는 `**`
// 바로 뒤에 공백 없이 한글이 붙으면 강조로 인식하지 않는데(한국어는 조사를 띄어쓰지
// 않으므로 report_md에서 구조적으로 계속 발생), 이 플러그인이 그 판정을 완화한다.
// 한 곳에서만 옳게 적으면 되도록 모았다.
//
// components를 받는 이유: 세부기술 보고서(Report.tsx)만 각주 앵커용 `a` 렌더러를
// 얹는다. 나머지 화면은 기본값을 그대로 쓴다.
export default function Prose({
  md,
  className = "",
  components = MARKDOWN_COMPONENTS,
}: {
  md: string;
  className?: string;
  components?: Components;
}) {
  return (
    <div className={`report-prose ${PROSE_CLASSES}${className ? ` ${className}` : ""}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkCjkFriendly]} components={components}>
        {md}
      </ReactMarkdown>
    </div>
  );
}

