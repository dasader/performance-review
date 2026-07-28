// 당해연도 분석 진행 파이. 세부기술 수만큼 원을 등분하고, 분석이 끝난 수만큼 칠한다.
// 미분석 조각은 칠하지 않고 점선 테두리만 둔다 — "아직 없음"과 "0건"을 구분한다.
//
// recharts를 쓰지 않는다. 랜딩(FieldList)은 즉시 로드되는 첫 화면이라 차트 라이브러리를
// 끌어오면 초기 번들이 커지는데, 등분 파이는 SVG 경로 몇 줄이면 된다.

// 12시 방향에서 시작해 시계 방향. SVG 좌표계는 y가 아래로 증가하므로 각도에서 90°를 뺀다.
function point(cx: number, cy: number, r: number, deg: number): [number, number] {
  const rad = ((deg - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

function slicePath(cx: number, cy: number, r: number, from: number, to: number): string {
  const [x1, y1] = point(cx, cy, r, from);
  const [x2, y2] = point(cx, cy, r, to);
  // large-arc 플래그: 180°를 넘는 조각인지. 세부기술이 1개면 조각이 360°가 되는데
  // 그때는 시작점과 끝점이 같아 호가 그려지지 않으므로 원으로 따로 그린다.
  const largeArc = to - from > 180 ? 1 : 0;
  return `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2} Z`;
}

export default function ProgressPie({
  total,
  done,
  size = 48,
}: {
  total: number;
  done: number;
  size?: number;
}) {
  if (total <= 0) return null;

  const r = size / 2 - 1; // 테두리 두께만큼 안으로
  const c = size / 2;
  const filled = Math.min(done, total);
  const label = `세부기술 ${total}개 중 ${filled}개 분석 완료`;

  // 세부기술이 1개면 조각 하나가 원 전체다 — 호로는 못 그리니 <circle>로 처리한다.
  const slices =
    total === 1 ? null : (
      Array.from({ length: total }, (_, i) => {
        const step = 360 / total;
        const isDone = i < filled;
        return (
          <path
            key={i}
            d={slicePath(c, c, r, i * step, (i + 1) * step)}
            className={isDone ? "fill-accent stroke-accent" : "fill-none stroke-muted"}
            strokeWidth={1.25}
            strokeDasharray={isDone ? undefined : "3 2"}
          />
        );
      })
    );

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={label}
      className="shrink-0"
    >
      <title>{label}</title>
      {slices ?? (
        <circle
          cx={c}
          cy={c}
          r={r}
          className={filled ? "fill-accent stroke-accent" : "fill-none stroke-muted"}
          strokeWidth={1.25}
          strokeDasharray={filled ? undefined : "3 2"}
        />
      )}
    </svg>
  );
}
