import { useEffect, useState } from "react";
import { ApiError, enqueueComparison, get, type AdminSubfield } from "../api";

// 국가 비교 보고서 생성. FieldReportsPanel과 같은 규약 — 생성은 큐잉이고
// 실제 LLM 호출은 잡 루프가 한 틱에 하나씩 처리한다.
//
// 일괄 실행은 여기 두지 않는다 — "국가 현황" 탭의 ComparisonGrid가 그 역할을
// 한다(설정된 국가 전체를 pairs|all로 일괄 큐잉). 여기는 임의 조합을 만드는
// 유일한 통로로 남는다.
export default function ComparisonPanel({
  adminKey,
  onUnauthorized,
}: {
  adminKey: string;
  onUnauthorized: () => void;
}) {
  const currentYear = new Date().getFullYear();
  const [subfields, setSubfields] = useState<AdminSubfield[] | null>(null);
  const [subfieldId, setSubfieldId] = useState<number | null>(null);
  const [year, setYear] = useState(currentYear);
  // 스케줄 카드의 countries 입력과 같은 형식(콤마 구분) — 새 위젯을 만들지 않는다.
  const [countries, setCountries] = useState("KR,CN");
  const [error, setError] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    get<AdminSubfield[]>("/admin/subfields", adminKey)
      .then((all) => {
        // 비활성 세부기술은 분석이 없으므로 비교 대상이 될 수 없다.
        const active = all.filter((s) => s.active);
        setSubfields(active);
        setSubfieldId(active[0]?.id ?? null);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) return onUnauthorized();
        setError(e instanceof Error ? e.message : "세부기술 목록을 불러오지 못했습니다.");
      });
  }, [adminKey, onUnauthorized]);

  const submit = async () => {
    if (subfieldId === null) return;
    const codes = countries
      .split(",")
      .map((c) => c.trim().toUpperCase())
      .filter(Boolean);

    setBusy(true);
    setError(null);
    setLink(null);
    try {
      await enqueueComparison(subfieldId, year, codes, adminKey);
      setLink(`/subfields/${subfieldId}/compare/${year}?countries=${codes.join(",")}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onUnauthorized();
      // 409(분석이 없는 국가)는 관리자가 바로 고칠 수 있는 오류라 — 그 국가를 먼저
      // 실행하면 된다 — 서버 메시지를 그대로 보여준다. "실패했습니다"로 뭉뚱그리면
      // 어느 국가가 빠졌는지 알 수 없다.
      setError(e instanceof Error ? e.message : "비교 생성 요청에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-6 border border-border bg-surface p-4">
      <h2 className="text-lg font-semibold text-accent">국가 비교 보고서 생성</h2>

      <p className="mt-2 text-xs text-muted">
        요청한 모든 국가에 그 연도의 완성된 분석이 있어야 합니다 — 하나라도 없으면
        거부됩니다(일부만으로 만들면 "그 국가는 성과가 없다"로 오독됩니다).
        생성은 잡 루프가 한 틱에 하나씩 처리합니다.
      </p>

      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="text-sm text-muted">
          세부기술
          <select
            value={subfieldId ?? ""}
            onChange={(e) => setSubfieldId(Number(e.target.value))}
            className="input ml-1"
          >
            {subfields?.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>

        <label className="text-sm text-muted">
          연도
          <input
            type="number"
            value={year}
            onChange={(e) => setYear(Number(e.target.value))}
            className="input ml-1 w-24"
          />
        </label>

        <label className="text-sm text-muted">
          국가(콤마 구분)
          <input
            value={countries}
            onChange={(e) => setCountries(e.target.value)}
            className="input ml-1 w-40"
            placeholder="KR,CN"
          />
        </label>

        <button
          type="button"
          onClick={submit}
          disabled={busy || subfieldId === null}
          className="btn btn-primary btn-sm"
        >
          비교 생성
        </button>
      </div>

      {error && <p className="mt-3 banner banner-risk">{error}</p>}
      {/* 성공은 배너로 칠하지 않는다 — index.css의 "정상은 칠하지 않는다"(banner-ok를
          두지 않은 이유)를 따른다. */}
      {link && (
        <p className="mt-3 text-sm text-muted">
          큐잉했습니다.{" "}
          <a href={link} className="underline">
            비교 보고서 보기
          </a>
        </p>
      )}
    </section>
  );
}
