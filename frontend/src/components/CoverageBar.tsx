// 이 서비스의 핵심 원칙을 시각화하는 서명 컴포넌트: 검색된 모집단과 실제 분석된 표본은
// 다르다(OpenAlex는 abstract가 약 18% 누락된다). 그 차이를 막대 하나로 숨기지 않고 보여준다.
// 색만으로 전달하지 않도록 숫자 텍스트를 항상 함께 붙인다.
export default function CoverageBar({
  searched,
  analyzed,
  size = "md",
}: {
  searched: number;
  analyzed: number;
  size?: "sm" | "md";
}) {
  const pct = searched > 0 ? Math.round((analyzed / searched) * 100) : 0;
  const height = size === "sm" ? "h-1.5" : "h-2";

  return (
    <div className="flex items-center gap-3">
      <div
        className={`min-w-0 flex-1 ${height} border border-border bg-border-light`}
        role="img"
        aria-label={`분석 대상 비율 ${pct}%`}
      >
        <div className="h-full bg-accent" style={{ width: `${pct}%` }} />
      </div>
      <span className="shrink-0 whitespace-nowrap font-mono text-xs tabular-nums text-muted">
        {pct}%
      </span>
    </div>
  );
}
