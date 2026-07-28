// 보고서 생성 시각 표기. 컴포넌트 파일에서 내보내면 fast refresh가 깨져(oxlint
// react/only-export-components) 순수 함수는 여기 둔다.
export function formatGeneratedAt(iso: string | null): string {
  // pending 첫 생성 행은 generated_at이 없다 — 이 함수는 done 표시에만 쓰이지만
  // 타입 안전하게 null을 흡수한다.
  if (!iso) return "-";
  return new Date(iso).toLocaleString("ko-KR", { dateStyle: "medium", timeStyle: "short" });
}
