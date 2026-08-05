import { useEffect, useState } from "react";

// 조회 연도 입력 — 값이 확정될 때만 바깥에 알린다.
//
// 이 값은 곧 조회 키다(분야 보고서 현황·국가 격자 둘 다 year로 서버를 다시 읽는다).
// 타이핑마다 setYear를 부르면 "2025"를 입력하는 동안 2 · 20 · 202 · 2025로 네 번
// 요청이 나가고, 칸을 비우면 Number("")가 0이 되어 ?year=0으로도 한 번 더 나간다.
// 게다가 격자 쪽은 그때마다 5초 폴링 타이머까지 새로 건다.
//
// 그래서 입력 중에는 문자열 초안만 들고 있다가 blur(또는 Enter)에서 확정한다.
// 범위를 벗어난 값은 확정하지 않고 직전 값으로 되돌린다 — 서버가 422로 거절할
// 값을 굳이 보내지 않는다.
export default function YearInput({
  label = "대상 연도",
  year,
  onChange,
}: {
  label?: string;
  year: number;
  onChange: (year: number) => void;
}) {
  const [draft, setDraft] = useState(String(year));
  useEffect(() => setDraft(String(year)), [year]);

  const commit = () => {
    const next = Number(draft);
    if (Number.isInteger(next) && next >= 1900 && next <= 2100) onChange(next);
    else setDraft(String(year));
  };

  return (
    <label className="text-sm text-muted">
      {label}{" "}
      <input
        type="number"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
        }}
        className="input ml-1 w-24"
      />
    </label>
  );
}
