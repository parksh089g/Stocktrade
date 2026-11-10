// 데이터 창구 고르기. 데모 데이터는 `npm run demo`(mode=demo)에서만 쓰고 운영 빌드에는 들어가지 않는다.
import type { DataSource } from "./source";
import { createSupabaseSource } from "./supabase";

export type SourceResult = { source: DataSource } | { problem: string };

/** 관리자 키(sb_secret_, 예전 service_role JWT)인지. 웹앱에 넣으면 누구나 모든 기록을 읽고 쓸 수 있다. */
export function isAdminKey(key: string): boolean {
  if (key.startsWith("sb_secret_")) return true;
  const parts = key.split(".");
  if (parts.length !== 3) return false;
  try {
    const payload = JSON.parse(atob((parts[1] ?? "").replace(/-/g, "+").replace(/_/g, "/")));
    return payload?.role === "service_role";
  } catch {
    return false;
  }
}

export async function createSource(): Promise<SourceResult> {
  if (import.meta.env.MODE === "demo") {
    const { createDemoSource } = await import("./demo");
    return { source: createDemoSource() };
  }
  const url = import.meta.env.VITE_SUPABASE_URL;
  const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !key)
    return { problem: "Supabase 설정이 없습니다. webapp/.env.local에 VITE_SUPABASE_URL과 VITE_SUPABASE_PUBLISHABLE_KEY를 넣고 다시 빌드하세요." };
  if (isAdminKey(key))
    return { problem: "관리자 키(secret)가 들어 있습니다. 웹앱에는 publishable 키(sb_publishable_...)만 넣어야 합니다. 키를 바꾸고 다시 빌드하세요." };
  return { source: createSupabaseSource(url, key) };
}
