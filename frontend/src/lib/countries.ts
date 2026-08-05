// 백엔드 prompts.COUNTRY_NAMES와 같은 표. 화면이 국가 코드를 보여줄 일이
// 여러 곳(국가 줄·관리자 격자)이라 한 곳에 둔다.
export const COUNTRY_NAMES: Record<string, string> = {
  KR: "한국", US: "미국", CN: "중국", JP: "일본", DE: "독일",
  GB: "영국", FR: "프랑스", TW: "대만", IN: "인도", CA: "캐나다",
};

// 화면에 국가를 나열하는 고정 순서. 코드 정렬(CN·JP·KR·US)은 읽는 사람에게 아무
// 의미가 없다 — 한국이 기준국이라 맨 앞이고, 나머지는 비교에서 자주 보는 순서다.
//
// 여기 없는 국가는 뒤에 이름순으로 붙는다. 국가를 늘릴 때 이 배열만 고치면 되고,
// 빠뜨려도 화면에서 사라지지 않는다.
const DISPLAY_ORDER = ["KR", "US", "CN", "JP"];

export function sortCountries(codes: string[]): string[] {
  return [...codes].sort((a, b) => {
    const ia = DISPLAY_ORDER.indexOf(a);
    const ib = DISPLAY_ORDER.indexOf(b);
    if (ia !== -1 && ib !== -1) return ia - ib;
    if (ia !== -1) return -1;
    if (ib !== -1) return 1;
    return (COUNTRY_NAMES[a] ?? a).localeCompare(COUNTRY_NAMES[b] ?? b, "ko");
  });
}
