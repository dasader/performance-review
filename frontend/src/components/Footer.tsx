export default function Footer() {
  return (
    <footer className="mt-16 border-t border-border py-6 print:hidden">
      <div className="mx-auto max-w-5xl px-6 text-xs text-faint">
        논문 데이터 출처: OpenAlex, KCI. 인용수는 수집 시점 스냅샷 기준으로 이후 변동될 수
        있습니다.
      </div>
    </footer>
  );
}
