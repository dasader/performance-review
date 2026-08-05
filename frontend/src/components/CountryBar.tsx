import { Link } from "react-router-dom";
import { COUNTRY_NAMES, sortCountries } from "../lib/countries";

const BASE = "KR";

function name(code: string): string {
  return COUNTRY_NAMES[code] ?? code;
}

// 비교 이름표. 한국은 기준국이라 이름에서 뺀다 — "중국 vs 일본 vs 한국 vs 미국"처럼
// 늘어놓으면 4개국에서 글자가 20자를 넘겨 버튼 하나가 줄 전체를 먹는다(사용자 신고).
// 한국이 없는 조합(관리자가 콤마 입력으로 만든 US,CN 같은 것)은 전부 적는다.
function comparisonLabel(codes: string[]): string {
  const others = codes.filter((c) => c !== BASE);
  return sortCountries(others.length ? others : codes).map(name).join(" · ");
}

// 보고서 상단의 국가·비교 이동 줄.
//
// 두 줄로 나눈 이유: 성격이 다른 두 동작이다. 위는 "지금 어느 나라 보고서를 보는가"라
// 서로 배타적인 상태 전환이고(그래서 눌린 상태를 가진 토글), 아래는 "다른 종류의 문서로
// 간다"는 이동이다. 한 줄에 같은 모양으로 늘어놓으면 4개국에서 버튼이 8개가 되어
// 무엇이 무엇인지 읽히지 않는다(사용자 신고).
//
// **보유하지 않은 국가는 아예 그리지 않는다.** 공개 화면 방문자에게 "아직 안 돌렸다"는
// 운영 사정을 보일 이유가 없다 — 그 정보가 필요한 관리자는 관리자 격자에서 전부 본다.
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

  // 표시 순서는 lib/countries의 한 곳에서 정한다(한국·미국·중국·일본).
  const ordered = sortCountries(countries);

  // 모든 비교가 한국을 낀 경우에만 "한국과 비교"라고 말할 수 있다.
  const allWithBase = comparisons.every((c) => c.countries.includes(BASE));

  return (
    // 여백은 이 컴포넌트가 스스로 진다 — 바깥에서 감싸면 null을 반환하는 흔한
    // 단일 국가 경우에도 빈 줄이 남는다(리뷰 지적).
    //
    // 한 줄에 인라인으로 둔다. 국가와 비교는 머리말(국가 / 한국과 비교)로 갈리고,
    // 조작물 자체는 명도로 갈린다 — 국가는 눌린 상태를 가진 토글(btn-toggle),
    // 비교는 눌림이 없는 이동 버튼(btn-neutral). 둘 다 버튼이라 손으로 누르는
    // 물건이라는 신호는 같다.
    // mb-6: 아래 구분선을 없앤 자리라(원래 my-10) 본문과 바로 붙었다. 위(16px)보다
    // 아래(24px)를 넓게 둬 이 줄이 본문이 아니라 머리 쪽에 속한 것으로 읽히게 한다.
    <div className="mb-6 mt-4 flex flex-wrap items-center gap-2 print:hidden">
      {countries.length > 1 && (
        <>
          {/* eyebrow 모양을 쓰지 않는다 — 제목 블록에 이미 하나 있고, 같은 대문자
              라벨이 화면에 둘이면 어느 쪽이 상위 분류인지 읽히지 않는다
              (Report.tsx 헤더 주석의 규칙). */}
          <span className="text-xs text-muted">국가</span>
          {ordered.map((c) => (
            <Link
              key={c}
              to={`/subfields/${subfieldId}/${year}?country=${c}`}
              aria-pressed={c === current}
              className="btn btn-toggle btn-sm"
            >
              {name(c)}
            </Link>
          ))}
        </>
      )}

      {comparisons.length > 0 && (
        <>
          {/* 국가 버튼들과 붙어 보이지 않게 머리말 앞에만 여백을 준다. */}
          <span className="ml-2 text-xs text-muted">
            {allWithBase ? "한국과 비교" : "비교"}
          </span>
          {comparisons.map((cmp) => (
            <Link
              key={cmp.countries.join(",")}
              to={`/subfields/${subfieldId}/compare/${year}?countries=${cmp.countries.join(",")}`}
              className="btn btn-neutral btn-sm"
            >
              {comparisonLabel(cmp.countries)}
            </Link>
          ))}
        </>
      )}
    </div>
  );
}
