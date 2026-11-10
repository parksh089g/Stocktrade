/// <reference types="vite/client" />

// vite.config.ts의 define으로 빌드할 때 들어가는 값
declare const __SYMBOL_NAMES__: Record<string, string>;
declare const __APP_VERSION__: string;
declare const __BUILT_AT__: string;

interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL?: string;
  readonly VITE_SUPABASE_PUBLISHABLE_KEY?: string;
  readonly VITE_VAPID_PUBLIC_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
