/// <reference types="vite/client" />

// vite.config.ts의 define으로 빌드 타임에 package.json version이 문자열로 주입된다.
declare const __APP_VERSION__: string;
