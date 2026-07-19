// 상태는 점 색상 + 텍스트를 함께 보여준다 — 색만으로 정보를 전달하지 않는다.
const DOT_CLASS: Record<string, string> = {
  done: "bg-positive",
  failed: "bg-danger",
  paused: "bg-warning",
  pending: "bg-faint",
  searching: "bg-accent",
  extracting: "bg-accent",
  reducing: "bg-accent",
};

export default function StatusBadge({ status, label }: { status: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-ink-light">
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT_CLASS[status] ?? "bg-faint"}`}
      />
      {label}
    </span>
  );
}
