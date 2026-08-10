import { STATUS_DOT_CLASS } from "../lib/status";

// 상태는 점 색상 + 텍스트를 함께 보여준다 — 색만으로 정보를 전달하지 않는다.
// (텍스트가 항상 있으므로 점이 없어도 정보는 온전하다.)
// 어느 상태에 점을 찍는지와 그 근거는 lib/status.ts::STATUS_DOT_CLASS에 있다.
export default function StatusBadge({ status, label }: { status: string; label: string }) {
  const dot = STATUS_DOT_CLASS[status];
  return (
    <span className="inline-flex items-center gap-2 text-xs font-medium text-ink-light">
      {dot && <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />}
      {label}
    </span>
  );
}
