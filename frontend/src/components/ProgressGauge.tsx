// 당해연도 분석 진행률. 세부기술 수 대비 완료 수를 얇은 막대 하나로 보여준다.
//
// 원래는 등분 파이(ProgressPie)였는데, 완료 조각을 잉크로 칠하니 **다 끝난 분야일수록
// 시커먼 원반**이 되어 목록에서 가장 먼저 눈에 들어왔다. 정상이 기본값인데 정상이
// 제일 시끄러운 셈이라 "화면에서 눈에 띄는 것이 곧 봐야 할 것"과 정반대였다.
// 얇은 게이지는 100%일 때 조용한 한 줄이고, 덜 된 분야에서만 빈 구간이 눈에 띈다.
//
// 스케일이 있는 값이므로 칩이 아니라 게이지다 — 확인 채널은 하나이고, 정확한 건수는
// 바로 옆 행 글자("세부기술 5개 · 2026년 분석 5개")가 이미 말한다.
export default function ProgressGauge({ total, done }: { total: number; done: number }) {
  if (total <= 0) return null;

  // done이 total을 넘는 경우(세부기술 비활성화 등으로 어긋날 때)에도 100%를 넘지 않는다.
  const filled = Math.min(Math.max(done, 0), total);
  const pct = Math.round((filled / total) * 100);

  return (
    <span
      className="flex w-20 shrink-0 items-center gap-2"
      role="img"
      aria-label={`세부기술 ${total}개 중 ${filled}개 분석 완료`}
    >
      <span className="h-1 flex-1 bg-border-light">
        <span className="block h-full bg-ink" style={{ width: `${pct}%` }} />
      </span>
      <span className="w-8 shrink-0 text-right text-eyebrow tabular-nums text-muted">{pct}%</span>
    </span>
  );
}
