import { Link } from "react-router-dom";
import { COUNTRY_NAMES } from "../lib/countries";

// 보고서 상단의 국가·비교 이동 줄. Report.tsx가 이미 415줄이고 이 줄은 본문과
// 무관한 이동 수단이라 파일을 나눈다.
//
// **보유하지 않은 국가는 아예 그리지 않는다**(비활성 표시가 아니라 미표시).
// 공개 화면 방문자에게 "아직 안 돌렸다"는 운영 사정을 보일 이유가 없다 —
// 그 정보가 필요한 사람은 관리자이고 관리자 격자가 전부 보여준다.
export default function CountryBar({
  subfieldId,
  year,
  current,
  countries,
  comparisons,
}: {
  subfieldId: number;
  year: number;
  current: string;
  countries: string[];
  comparisons: { countries: string[]; label: string }[];
}) {
  // 고를 것이 없으면 줄 자체를 숨긴다(한국뿐이고 비교도 없는 경우).
  if (countries.length < 2 && comparisons.length === 0) return null;

  return (
    // 여백은 이 컴포넌트가 스스로 진다 — Report.tsx가 바깥에서 mt-4를 감싸면
    // null을 반환하는 흔한 단일 국가 경우에도 빈 줄이 16px 남는다(리뷰 지적).
    <div className="mt-4 flex flex-wrap items-center gap-2 print:hidden">
      {countries.map((c) => (
        <Link
          key={c}
          to={`/subfields/${subfieldId}/${year}?country=${c}`}
          aria-pressed={c === current}
          className="btn btn-toggle btn-sm"
        >
          {COUNTRY_NAMES[c] ?? c}
        </Link>
      ))}
      {comparisons.map((cmp) => (
        <Link
          key={cmp.countries.join(",")}
          to={`/subfields/${subfieldId}/compare/${year}?countries=${cmp.countries.join(",")}`}
          className="btn btn-neutral btn-sm"
        >
          {cmp.label}
        </Link>
      ))}
    </div>
  );
}
