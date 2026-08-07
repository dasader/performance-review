import { useEffect, useState } from "react";
import { Link } from "react-router";
import { get, type Field } from "../api";
import TopBar from "../components/TopBar";
import Footer from "../components/Footer";
import ProgressGauge from "../components/ProgressGauge";

export default function FieldList() {
  const [fields, setFields] = useState<Field[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<Field[]>("/fields").then(setFields).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="min-h-screen">
      <TopBar />
      <main className="mx-auto max-w-page px-6 pb-10 pt-6">
        {/* eyebrow — 11px 대문자·자간 확대라 그 크기에서도 읽힌다. 색은 빼고 명도로만 낮춘다.
            (이전에는 남색이었는데, 크롬에 채도가 새면 색이 정보를 뜻한다는 규칙이 무너진다) */}
        <p className="text-eyebrow font-bold uppercase tracking-[0.09em] text-muted">
          국가전략기술 논문성과
        </p>
        <h1 className="mt-1 text-3xl font-extrabold tracking-tight text-ink">
          10대 전략기술 분야 성과 보고서
        </h1>
        <p className="mt-3 text-sm text-ink-light">
          분야를 선택하면 연도·세부기술별로 검색된 한국 논문과 실제 분석에 사용된 논문 수를
          함께 확인하고, 통계·서술이 담긴 보고서를 열람할 수 있습니다.
        </p>

        {error && <p className="mt-6 text-sm text-danger">{error}</p>}

        {!fields && !error && <p className="mt-6 text-sm text-muted">불러오는 중…</p>}

        {fields && (
          // 10개 분야는 "병렬 비교되는 동급 덩어리"라 면(--surface)을 쓴다. 다만 카드를
          // gap으로 흩어 놓지 않고 1px 괘선 틈으로 붙여 하나의 격자로 만든다 —
          // 흩어 놓으면 카드 10장이 각자 떠 있고, 붙이면 한 표로 읽힌다.
          <ul className="mt-6 grid gap-px border border-border bg-border sm:grid-cols-2">
            {fields.map((f, i) => {
              const activeCount = f.subfields.filter((s) => s.active).length;
              return (
                <li key={f.id} className="bg-surface">
                  <Link
                    to={`/fields/${f.id}`}
                    className="flex items-center gap-4 p-4 transition-colors hover:bg-sunken"
                  >
                    <span className="text-sm tabular-nums text-muted">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-base font-semibold text-ink">{f.name}</span>
                      <span className="mt-1 block text-xs text-muted">
                        세부기술 {activeCount}개 · {f.current_year}년 분석 {f.current_year_done}개
                      </span>
                    </span>
                    <ProgressGauge total={activeCount} done={f.current_year_done} />
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </main>
      <Footer />
    </div>
  );
}
