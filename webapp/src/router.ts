// 작은 라우터. 주소는 /runs/123처럼 경로를 그대로 쓴다(엔진 푸시 알림이 여는 주소).
// Cloudflare Pages는 404.html이 없으면 모든 경로에 index.html을 돌려줘서 바로 열어도 된다.
import { useEffect, useState } from "preact/hooks";

const listeners = new Set<() => void>();
let inAppSteps = 0;

/** 이 앱 안에서 이동한 적이 있어 뒤로 가기가 앱 안에 머무는지 */
export function canGoBack(): boolean {
  return inAppSteps > 0;
}

export function goBack(fallback: string): void {
  if (canGoBack()) {
    inAppSteps -= 1;
    history.back();
  } else navigate(fallback, { replace: true });
}

export function navigate(to: string, opts: { replace?: boolean } = {}): void {
  const url = new URL(to, location.href);
  if (url.origin !== location.origin) {
    location.href = url.href;
    return;
  }
  const next = url.pathname + url.search;
  if (next !== location.pathname + location.search) {
    if (opts.replace) history.replaceState(null, "", next);
    else {
      history.pushState(null, "", next);
      inAppSteps += 1;
    }
  }
  for (const listener of listeners) listener();
  window.scrollTo(0, 0);
}

export function usePath(): string {
  const [path, setPath] = useState(location.pathname);
  useEffect(() => {
    const update = () => setPath(location.pathname);
    listeners.add(update);
    window.addEventListener("popstate", update);
    return () => {
      listeners.delete(update);
      window.removeEventListener("popstate", update);
    };
  }, []);
  return path;
}

/** 같은 사이트 안의 <a href> 클릭은 페이지를 다시 읽지 않고 화면만 바꾼다. */
export function interceptLinks(): void {
  document.addEventListener("click", (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = (e.target as Element | null)?.closest?.("a");
    if (!a || a.target || a.hasAttribute("download") || a.origin !== location.origin) return;
    e.preventDefault();
    navigate(a.pathname + a.search);
  });
}

/** '/runs/:id' 같은 모양과 맞춰 본다. 맞으면 이름별 값, 아니면 null */
export function matchPath(pattern: string, path: string): Record<string, string> | null {
  const want = pattern.split("/");
  const got = path.replace(/\/+$/, "").split("/");
  if (want.length !== got.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < want.length; i++) {
    const w = want[i] as string;
    const g = got[i] as string;
    if (w.startsWith(":")) params[w.slice(1)] = decodeURIComponent(g);
    else if (w !== g) return null;
  }
  return params;
}
