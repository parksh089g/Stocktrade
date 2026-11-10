import { createContext } from "preact";
import { useContext, useEffect, useRef, useState } from "preact/hooks";
import type { DataSource, User } from "./data/source";
import type { Mode, SwitchState } from "./lib/types";

export interface AppState {
  source: DataSource;
  user: User;
  mode: Mode;
  /** 보는 기록을 고른다. null이면 기본값(가장 최근 실행의 모드)으로 되돌린다 */
  setMode(mode: Mode | null): void;
  /** 새로 고침할 때마다 늘어난다. 화면들이 데이터를 다시 읽는 신호 */
  tick: number;
  refresh(): void;
  switchState: SwitchState;
  setSwitchState(state: SwitchState): void;
}

export const AppContext = createContext<AppState | null>(null);

export function useApp(): AppState {
  const app = useContext(AppContext);
  if (!app) throw new Error("AppContext가 없습니다");
  return app;
}

export interface Async<T> {
  data: T | undefined;
  error: Error | null;
  loading: boolean;
}

/**
 * 비동기로 읽은 값. 다시 읽는 동안 이전 값을 그대로 보여 준다(화면이 깜빡이지 않게).
 * deps가 바뀌면 다시 읽고, 늦게 끝난 이전 요청의 결과는 버린다.
 */
export function useAsync<T>(load: () => Promise<T>, deps: readonly unknown[]): Async<T> {
  const [state, setState] = useState<Async<T>>({ data: undefined, error: null, loading: true });
  const seq = useRef(0);
  useEffect(() => {
    const mine = ++seq.current;
    setState((s) => ({ ...s, loading: true }));
    load().then(
      (data) => {
        if (seq.current === mine) setState({ data, error: null, loading: false });
      },
      (err: unknown) => {
        if (seq.current === mine)
          setState((s) => ({ ...s, error: err instanceof Error ? err : new Error(String(err)), loading: false }));
      },
    );
    // eslint 규칙 없이 deps를 그대로 넘긴다
  }, deps);
  return state;
}

const MODE_KEY = "stocktrade.mode";

export function readStoredMode(): Mode | null {
  try {
    const v = localStorage.getItem(MODE_KEY);
    return v === "paper" || v === "live" ? v : null;
  } catch {
    return null;
  }
}

export function storeMode(mode: Mode | null): void {
  try {
    if (mode) localStorage.setItem(MODE_KEY, mode);
    else localStorage.removeItem(MODE_KEY);
  } catch {
    // 저장소를 못 쓰면(사생활 보호 모드 등) 이번 실행에서만 기억한다
  }
}
