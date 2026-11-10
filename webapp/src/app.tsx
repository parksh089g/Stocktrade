// 앱 틀: 로그인 → 소유자 확인 → 상단 바 + 화면 + 하단 탭.
import { useEffect, useState } from "preact/hooks";
import { Banner, Icons, Loading } from "./components/ui";
import type { DataSource, User } from "./data/source";
import { AppContext, readStoredMode, storeMode, useApp, useAsync, type AppState } from "./hooks";
import type { Mode, SwitchState } from "./lib/types";
import { goBack, matchPath, usePath } from "./router";
import { Home } from "./screens/Home";
import { Login } from "./screens/Login";
import { NotOwner } from "./screens/NotOwner";
import { Orders } from "./screens/Orders";
import { Performance } from "./screens/Performance";
import { Positions } from "./screens/Positions";
import { RunDetail } from "./screens/RunDetail";
import { Runs } from "./screens/Runs";
import { Settings } from "./screens/Settings";

const TABS = [
  { path: "/", label: "홈", icon: Icons.home, title: "주식 자동매매" },
  { path: "/positions", label: "보유", icon: Icons.holdings, title: "보유 종목" },
  { path: "/orders", label: "주문", icon: Icons.orders, title: "주문" },
  { path: "/runs", label: "알림함", icon: Icons.inbox, title: "알림함" },
  { path: "/performance", label: "성적", icon: Icons.chart, title: "성적" },
];

// 앱으로 돌아왔을 때 이보다 오래 지났으면 새로 읽는다
const REFRESH_AFTER_MS = 30_000;

function Splash() {
  return (
    <div class="center-page">
      <Loading />
    </div>
  );
}

export function ConfigProblem({ message }: { message: string }) {
  return (
    <div class="center-page">
      <Banner tone="critical">
        <strong>설정 문제</strong>
        {message}
      </Banner>
    </div>
  );
}

export function App({ source }: { source: DataSource }) {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    source.currentUser().then(
      (u) => alive && setUser(u),
      () => alive && setUser(null),
    );
    const off = source.onAuthChange((u) => setUser(u));
    return () => {
      alive = false;
      off();
    };
  }, [source]);

  if (user === undefined) return <Splash />;
  if (user === null) return <Login source={source} />;
  return <Signed key={user.id} source={source} user={user} />;
}

function Signed({ source, user }: { source: DataSource; user: User }) {
  const [tick, setTick] = useState(0);
  const [override, setOverride] = useState<Mode | null>(readStoredMode());
  const [switchState, setSwitchState] = useState<SwitchState | null | undefined>(undefined);
  const [error, setError] = useState<Error | null>(null);
  const latest = useAsync(() => source.latestMode(), [tick]);
  const refresh = () => setTick((t) => t + 1);

  useEffect(() => {
    let alive = true;
    source.getSwitch().then(
      (s) => {
        if (!alive) return;
        setSwitchState(s);
        setError(null);
      },
      (e: Error) => alive && setError(e),
    );
    return () => {
      alive = false;
    };
  }, [tick]);

  useEffect(() => {
    let last = Date.now();
    const onVisible = () => {
      if (document.visibilityState !== "visible" || Date.now() - last < REFRESH_AFTER_MS) return;
      last = Date.now();
      refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  if (switchState === undefined)
    return error ? (
      <div class="center-page">
        <Banner tone="critical">
          <strong>불러오지 못했습니다</strong>
          {error.message}
        </Banner>
        <button class="btn primary block" onClick={refresh}>
          다시 시도
        </button>
      </div>
    ) : (
      <Splash />
    );
  if (switchState === null) return <NotOwner source={source} user={user} onRetry={refresh} />;

  const app: AppState = {
    source,
    user,
    mode: override ?? latest.data ?? "paper",
    setMode: (mode) => {
      storeMode(mode);
      setOverride(mode);
    },
    tick,
    refresh,
    switchState,
    setSwitchState,
  };
  return (
    <AppContext.Provider value={app}>
      <Shell refreshError={error} />
    </AppContext.Provider>
  );
}

function route(path: string) {
  const run = matchPath("/runs/:id", path);
  if (run && /^\d+$/.test(run.id ?? "")) return { screen: <RunDetail id={Number(run.id)} />, title: "실행 기록", back: "/runs" };
  const tab = TABS.find((t) => t.path === path);
  if (path === "/settings") return { screen: <Settings />, title: "설정", back: null };
  if (path === "/") return { screen: <Home />, title: tab?.title ?? "", back: null };
  if (path === "/positions") return { screen: <Positions />, title: tab?.title ?? "", back: null };
  if (path === "/orders") return { screen: <Orders />, title: tab?.title ?? "", back: null };
  if (path === "/runs") return { screen: <Runs />, title: tab?.title ?? "", back: null };
  if (path === "/performance") return { screen: <Performance />, title: tab?.title ?? "", back: null };
  return {
    screen: (
      <Banner tone="info">
        <strong>없는 화면입니다</strong>
        <a href="/">홈으로</a>
      </Banner>
    ),
    title: "주식 자동매매",
    back: null,
  };
}

function Shell({ refreshError }: { refreshError: Error | null }) {
  const { source, mode, refresh } = useApp();
  const path = usePath();
  const [spinning, setSpinning] = useState(false);
  const { screen, title, back } = route(path);

  const onRefresh = () => {
    refresh();
    setSpinning(true);
    setTimeout(() => setSpinning(false), 700);
  };

  return (
    <div class="app">
      <header class="topbar">
        {back && (
          <button class="icon-btn" aria-label="뒤로" onClick={() => goBack(back)} style={{ marginLeft: "-8px" }}>
            {Icons.back()}
          </button>
        )}
        <h1>{title}</h1>
        <span class={`chip ${mode === "live" ? "critical" : ""}`}>{mode === "live" ? "실전" : "모의투자"}</span>
        <button class="icon-btn" aria-label="새로 고침" aria-busy={spinning} onClick={onRefresh}>
          {Icons.refresh()}
        </button>
        <a class="icon-btn" href="/settings" aria-label="설정" aria-current={path === "/settings" ? "page" : undefined}>
          {Icons.settings()}
        </a>
      </header>
      <main>
        {source.demo && path !== "/settings" && (
          <Banner tone="info">
            <strong>데모 데이터</strong>
            화면 확인용 가짜 기록입니다.
          </Banner>
        )}
        {refreshError && (
          <Banner tone="warning">
            <strong>새로 고치지 못했습니다</strong>
            <span class="small">{refreshError.message}</span>
          </Banner>
        )}
        {screen}
      </main>
      <footer class="tabbar">
        <nav aria-label="화면">
          {TABS.map((t) => {
            const active = t.path === "/" ? path === "/" : path.startsWith(t.path);
            return (
              <a key={t.path} href={t.path} aria-current={active ? "page" : undefined}>
                {t.icon()}
                <span>{t.label}</span>
              </a>
            );
          })}
        </nav>
      </footer>
    </div>
  );
}
