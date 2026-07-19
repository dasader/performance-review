import { Link } from "react-router-dom";

// 모든 화면 위에 얇게 깔리는 브랜드 바. 인쇄 시에는 보고서 본문만 남기고 숨긴다.
export default function TopBar() {
  return (
    <div className="border-b border-border bg-surface print:hidden">
      <div className="mx-auto flex h-14 max-w-5xl items-center justify-between px-6">
        <Link
          to="/"
          className="font-display text-base font-bold tracking-tight text-ink hover:text-accent"
        >
          전략기술 논문성과 분석
        </Link>
        <span className="hidden font-mono text-xs text-faint sm:inline">
          12대 국가전략기술 · OpenAlex · KCI
        </span>
      </div>
    </div>
  );
}
