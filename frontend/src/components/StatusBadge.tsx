// 상태는 점 색상 + 텍스트를 함께 보여준다 — 색만으로 정보를 전달하지 않는다.
//
// 색은 상태 4단(정상/주의/경고/위험)에서만 나온다. 진행 중(searching·extracting·
// reducing)은 그 넷 중 어느 것도 아니라 무채색 잉크로 둔다 — 여기에 파란색을 주면
// "색이 보이는 곳이 곧 정보"라는 규칙이 무너지고, 정상적으로 돌아가는 화면이
// 온통 색으로 뒤덮인다. 대기(pending)는 한 단 옅은 무채색으로 진행 중과 구분한다.
const DOT_CLASS: Record<string, string> = {
  done: "bg-positive-mark",
  failed: "bg-danger-mark",
  paused: "bg-warning-mark",
  pending: "bg-border-strong",
  searching: "bg-ink",
  extracting: "bg-ink",
  reducing: "bg-ink",
};

export default function StatusBadge({ status, label }: { status: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-xs font-medium text-ink-light">
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT_CLASS[status] ?? "bg-border-strong"}`}
      />
      {label}
    </span>
  );
}
