import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { get, type Field } from "../api";
import TopBar from "../components/TopBar";
import Footer from "../components/Footer";
import ProgressPie from "../components/ProgressPie";

export default function FieldList() {
  const [fields, setFields] = useState<Field[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<Field[]>("/fields").then(setFields).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="min-h-screen">
      <TopBar />
      <main className="mx-auto max-w-5xl px-6 py-14">
        <p className="font-mono text-xs uppercase tracking-widest text-accent">
          국가전략기술 논문성과
        </p>
        <h1 className="mt-2 font-display text-3xl font-bold tracking-tight text-ink sm:text-4xl">
          12대 전략기술 분야 성과 보고서
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-light">
          분야를 선택하면 연도·세부기술별로 검색된 한국 논문과 실제 분석에 사용된 논문 수를
          함께 확인하고, 통계·서술이 담긴 보고서를 열람할 수 있습니다.
        </p>

        {error && <p className="mt-8 text-sm text-danger">{error}</p>}

        {!fields && !error && <p className="mt-10 text-sm text-muted">불러오는 중…</p>}

        {fields && (
          <ul className="mt-10 grid gap-3 sm:grid-cols-2">
            {fields.map((f, i) => {
              const activeCount = f.subfields.filter((s) => s.active).length;
              return (
                <li key={f.id}>
                  <Link
                    to={`/fields/${f.id}`}
                    className="group flex items-start gap-4 border border-border bg-surface p-5 transition-colors hover:border-accent"
                  >
                    <span className="font-mono text-sm text-faint group-hover:text-accent">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block font-display font-semibold text-ink">{f.name}</span>
                      <span className="mt-1 block text-xs text-muted">
                        세부기술 {activeCount}개 · {f.current_year}년 분석 {f.current_year_done}개
                      </span>
                    </span>
                    <ProgressPie total={activeCount} done={f.current_year_done} />
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
