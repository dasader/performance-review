import { Link, useLocation } from "react-router-dom";

// 모든 화면 위에 얇게 깔리는 브랜드 바. 인쇄 시에는 보고서 본문만 남기고 숨긴다.
export default function TopBar() {
  const { pathname } = useLocation();
  // 이미 관리자 화면에 있으면 같은 곳으로 가는 진입 버튼을 또 보여주지 않는다.
  const isAdmin = pathname.startsWith("/admin");

  return (
    <div className="border-b border-border bg-surface print:hidden">
      <div className="mx-auto flex h-14 max-w-5xl items-center justify-between px-6">
        <Link
          to="/"
          className="font-display text-base font-bold tracking-tight text-ink hover:text-accent"
        >
          전략기술 논문성과 분석
        </Link>
        <div className="flex items-center gap-4">
          <span className="hidden font-mono text-xs text-faint sm:inline">
            12대 국가전략기술 · OpenAlex · KCI
          </span>
          {!isAdmin && (
            <Link
              to="/admin"
              className="inline-flex items-center gap-1.5 btn btn-neutral btn-sm"
            >
              <span aria-hidden="true">⚙</span>
              관리자
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
