import { useEffect, useRef, useState } from "react";
import { get, type SiteInfo, type VisitorStats } from "../api";

export default function Footer() {
  // domain은 SITE_DOMAIN(.env, 런타임)이 비어 있으면 window.location.host로 대체한다.
  const [domain, setDomain] = useState(() => window.location.host);
  const [visitors, setVisitors] = useState<VisitorStats | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    // 도메인 조회 실패 시 초기값(window.location.host)을 그대로 둔다 — 조용히 대체.
    get<SiteInfo>("/site-info")
      .then((info) => {
        if (info.domain) setDomain(info.domain);
      })
      .catch(() => {});

    // 방문자 수를 못 불러와도 푸터 자체는 깨지지 않고 이 영역만 생략한다.
    get<VisitorStats>("/visitors")
      .then(setVisitors)
      .catch(() => {});
  }, []);

  return (
    // 푸터는 면(--surface)으로 되올려 지면과 갈라 놓는다 — 출처·스냅샷 시점을 적는
    // 자리이고, 데이터가 언제 것인지 모르는 화면은 신뢰할 수 없다.
    <footer className="mt-10 border-t border-border bg-surface py-4 print:hidden">
      <div className="mx-auto flex max-w-page flex-col gap-3 px-6 text-xs text-muted sm:flex-row sm:items-start sm:justify-between">
        <div>
          {/* mono는 한글이 섞인 문자열에 쓰지 않는다 — 도메인·버전만 mono로 남긴다. */}
          <p>
            전략기술 논문성과 분석 ·{" "}
            <span className="font-mono">
              {domain} · v{__APP_VERSION__}
            </span>
          </p>
          <p className="mt-1">
            논문 데이터 출처: OpenAlex, KCI. 인용수는 수집 시점 스냅샷 기준으로 이후 변동될 수
            있습니다.
          </p>
        </div>

        {visitors && (
          <VisitorPanel visitors={visitors} expanded={expanded} setExpanded={setExpanded} />
        )}
      </div>
    </footer>
  );
}

function VisitorPanel({
  visitors,
  expanded,
  setExpanded,
}: {
  visitors: VisitorStats;
  expanded: boolean;
  setExpanded: (update: boolean | ((v: boolean) => boolean)) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);

  // 패널이 열려 있을 때만 바깥 클릭/Esc를 감시한다 — 트리거 버튼 클릭은 containerRef 안쪽이라
  // 여기서 걸리지 않고 버튼 자신의 onClick으로만 토글된다(이중 토글 방지).
  useEffect(() => {
    if (!expanded) return;
    function onPointerDown(e: PointerEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setExpanded(false);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setExpanded(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [expanded, setExpanded]);

  return (
    // relative — 아래 패널이 "위로"(bottom-full) 뜰 때 이 트리거 기준으로 위치를 잡기 위함.
    // 푸터가 페이지 최하단에 있어 기본 펼침(아래로)이면 뷰포트 밖으로 잘리므로 drop-up으로 연다.
    <div ref={containerRef} className="relative shrink-0 text-right">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-controls="visitor-daily-panel"
        aria-label={`오늘 ${visitors.today}명, 이번 주 ${visitors.this_week}명 방문 — 일별 방문자 수 ${expanded ? "접기" : "펼치기"}`}
        className="tabular-nums text-muted underline decoration-border underline-offset-2 hover:text-ink hover:decoration-ink"
      >
        {/* 열림/닫힘을 색만으로 구분하지 않도록 화살표 방향도 함께 바꾼다. */}
        <span aria-hidden="true">{expanded ? "▾" : "▴"}</span> 오늘 {visitors.today.toLocaleString()} · 이번
        주 {visitors.this_week.toLocaleString()}
      </button>

      {expanded && (
        <ul
          id="visitor-daily-panel"
          aria-label="일별 방문자 수"
          className="absolute bottom-full right-0 z-10 mb-2 max-h-64 w-56 max-w-[calc(100vw-3rem)] space-y-1 overflow-y-auto border border-border bg-surface p-3 text-left text-eyebrow tabular-nums text-muted"
        >
          {visitors.daily.map((d) => (
            <li key={d.date} className="flex justify-end gap-3">
              <span>{d.date}</span>
              <span className="text-muted">{d.count.toLocaleString()}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
