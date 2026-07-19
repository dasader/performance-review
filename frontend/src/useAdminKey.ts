import { useState } from "react";

const STORAGE_KEY = "admin-key";

// sessionStorage: 탭을 닫으면 사라진다 — 관리자 키를 localStorage에 영구 보관하지 않는다.
export function useAdminKey() {
  const [key, setKey] = useState(() => sessionStorage.getItem(STORAGE_KEY) ?? "");

  const save = (value: string) => {
    sessionStorage.setItem(STORAGE_KEY, value);
    setKey(value);
  };
  const clear = () => {
    sessionStorage.removeItem(STORAGE_KEY);
    setKey("");
  };
  return { key, save, clear };
}
