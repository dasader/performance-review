import { readFileSync } from 'node:fs'
// 'vite'가 아니라 'vitest/config' — 아래 test 블록을 타입이 알아보게 하려면 이쪽이어야 한다
// (vite의 defineConfig는 test 키를 모르는 타입이라 tsc -b가 거부한다).
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// package.json의 version이 단일 출처 — 빌드 타임에 __APP_VERSION__으로 주입해
// 런타임 API 호출 없이 푸터에 표시한다.
const { version } = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf-8'))

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(version),
  },
  test: {
    // vitest는 src의 순수 함수 단위 테스트만 본다. e2e/*.spec.js는 @playwright/test를
    // import하는데 그 패키지는 로컬에 깔지 않는다(도커 이미지 안에서만 도는 스펙 —
    // 루트 CLAUDE.md 참고). 기본 include는 저장소 전체를 훑어 그 파일까지 수집했고,
    // 결과로 `npm test`가 늘 "1 failed"로 끝나 진짜 실패를 가렸다.
    include: ['src/**/*.test.ts'],
  },
})
