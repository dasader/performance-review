// 상태는 점 색상 + 텍스트를 함께 보여준다 — 색만으로 정보를 전달하지 않는다.
// (텍스트가 항상 있으므로 점이 없어도 정보는 온전하다.)
//
// 색은 상태 4단(정상/주의/경고/위험)에서만 나온다. 진행 중(searching·extracting·
// reducing)은 그 넷 중 어느 것도 아니라 무채색 잉크로 둔다 — 여기에 파란색을 주면
// "색이 보이는 곳이 곧 정보"라는 규칙이 무너지고, 정상적으로 돌아가는 화면이
// 온통 색으로 뒤덮인다. 대기(pending)는 한 단 옅은 무채색으로 진행 중과 구분한다.
// done은 여기 없다 — 정상은 칠하지 않는다(index.css가 .banner-ok를 두지 않은 것과 같은
// 규칙). 10행이 전부 "● 완료"면 점이 나르는 정보가 0인데, 체크박스까지 옆에 붙는
// 관리자 표에서는 눈에 걸리기만 한다. 점이 보이면 볼 것이 있다는 뜻이어야 한다.
const DOT_CLASS: Record<string, string> = {
  failed: "bg-danger-mark",
  paused: "bg-warning-mark",
  pending: "bg-border-strong",
  searching: "bg-ink",
  extracting: "bg-ink",
  reducing: "bg-ink",
};

export default function StatusBadge({ status, label }: { status: string; label: string }) {
  const dot = DOT_CLASS[status];
  return (
    <span className="inline-flex items-center gap-2 text-xs font-medium text-ink-light">
      {dot && <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />}
      {label}
    </span>
  );
}
