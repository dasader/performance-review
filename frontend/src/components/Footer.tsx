import { useEffect, useState } from "react";
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
    <footer className="mt-16 border-t border-border py-6 print:hidden">
      <div className="mx-auto flex max-w-5xl flex-col gap-3 px-6 text-xs text-faint sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="font-mono">
            전략기술 논문성과 분석 · {domain} · v{__APP_VERSION__}
          </p>
          <p className="mt-1">
            논문 데이터 출처: OpenAlex, KCI. 인용수는 수집 시점 스냅샷 기준으로 이후 변동될 수
            있습니다.
          </p>
        </div>

        {visitors && <VisitorPanel visitors={visitors} expanded={expanded} onToggle={() => setExpanded((v) => !v)} />}
      </div>
    </footer>
  );
}

function VisitorPanel({
  visitors,
  expanded,
  onToggle,
}: {
  visitors: VisitorStats;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="shrink-0 text-right">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        aria-label={`오늘 ${visitors.today}명, 이번 주 ${visitors.this_week}명 방문 — 일별 방문자 수 ${expanded ? "접기" : "펼치기"}`}
        className="font-mono tabular-nums text-faint underline decoration-border underline-offset-2 hover:text-muted hover:decoration-accent"
      >
        오늘 {visitors.today.toLocaleString()} · 이번 주 {visitors.this_week.toLocaleString()}
      </button>

      {expanded && (
        <ul className="mt-2 space-y-0.5 font-mono text-[11px] tabular-nums text-faint">
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
