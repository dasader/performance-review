import { Link, useLocation } from "react-router-dom";

// 모든 화면 위에 얇게 깔리는 브랜드 바. 인쇄 시에는 보고서 본문만 남기고 숨긴다.
//
// 크롬(헤더)만 잉크로 반전한다. 크롬과 본문이 색이 아니라 **명도**로 갈리므로
// "여기부터 내용"이 스크롤 어디서나 즉시 읽힌다. 흰 헤더에 얇은 괘선 한 줄이던
// 이전 형태는 본문 첫 카드와 밝기가 같아 경계가 사라졌다.
//
// 좌측 3px 마크가 이 서비스의 유일한 식별색 자리다. 지금은 무채색(흰색)을 쓴다 —
// 색을 넣는다면 --chrome(#18181b) 위에서 3:1을 넘겨야 하고, 화면 어디에도 다시
// 나오지 않아야 한다.
export default function TopBar() {
  const { pathname } = useLocation();
  // 이미 관리자 화면에 있으면 같은 곳으로 가는 진입 버튼을 또 보여주지 않는다.
  const isAdmin = pathname.startsWith("/admin");

  return (
    <div className="chrome-bar bg-chrome print:hidden">
      <div className="mx-auto flex h-14 max-w-page items-center justify-between gap-4 px-6">
        <Link to="/" className="flex min-w-0 items-center gap-3 text-chrome-ink">
          <span aria-hidden="true" className="h-5 w-[3px] shrink-0 bg-white" />
          <span className="truncate text-base font-bold tracking-tight">
            전략기술 논문성과 분석
          </span>
        </Link>
        <div className="flex shrink-0 items-center gap-4">
          <span className="hidden text-xs text-chrome-ink-2 sm:inline">
            12대 국가전략기술 · OpenAlex · KCI
          </span>
          {!isAdmin && (
            <Link
              to="/admin"
              className="btn btn-sm bg-chrome-rule text-border-light hover:bg-[#52525b] hover:text-white"
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
