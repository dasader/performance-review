import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";

// 화면 토글 상태를 URL 쿼리에 싣는다(?withSections=1). 상태를 컴포넌트 안에 두지
// 않는 이유는 공유·북마크·인쇄 — 켠 채로 링크를 넘기면 상대도 켜진 화면을 본다.
//
// 네 화면(세부 보고서·참고문헌·세부기술 첨부·1:1 대조)이 같은 다섯 줄을 각자
// 갖고 있었다. 플래그 이름만 다르다.
export function useQueryFlag(name: string): [boolean, () => void] {
  const [searchParams, setSearchParams] = useSearchParams();
  const on = searchParams.get(name) === "1";
  const toggle = () => {
    const next = new URLSearchParams(searchParams);
    if (on) next.delete(name);
    else next.set(name, "1");
    setSearchParams(next);
  };
  return [on, toggle];
}

// 진행 중일 때만 도는 폴링. 생성은 잡 루프가 한 틱(30초)에 하나씩 처리하므로
// 요청 즉시 끝나지 않는다 — 화면은 pending인 동안만 주기적으로 다시 읽는다.
//
// active가 false면 타이머를 아예 걸지 않고, 끝나면 반드시 해제한다. 세 화면이
// 이 네 줄을 각자 갖고 있었고, 정리(clearInterval)를 빠뜨리면 언마운트된 화면이
// 계속 서버를 두드린다.
export function usePolling(active: boolean, fn: () => void, ms = 5000) {
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(fn, ms);
    return () => clearInterval(timer);
  }, [active, fn, ms]);
}
