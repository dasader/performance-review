import { useCallback, useState } from "react";

const STORAGE_KEY = "admin-key";

// sessionStorage: 탭을 닫으면 사라진다 — 관리자 키를 localStorage에 영구 보관하지 않는다.
//
// save/clear를 useCallback으로 고정하는 이유: Admin.tsx의 onUnauthorized가 clear를
// 의존성으로 갖고, 그것이 loadDashboard의 의존성이며, 다시 대시보드 로딩 effect의
// 의존성이다. 매 렌더마다 새 함수가 나오면 이 사슬 전체가 매번 갈려 effect가
// 재실행되고 → setData → 렌더 → effect …로 /admin/dashboard를 무한히 재요청한다
// (자식 패널의 useEffect(load, [load])도 같은 사슬에 걸려 함께 돈다).
export function useAdminKey() {
  const [key, setKey] = useState(() => sessionStorage.getItem(STORAGE_KEY) ?? "");

  const save = useCallback((value: string) => {
    sessionStorage.setItem(STORAGE_KEY, value);
    setKey(value);
  }, []);
  const clear = useCallback(() => {
    sessionStorage.removeItem(STORAGE_KEY);
    setKey("");
  }, []);
  return { key, save, clear };
}
