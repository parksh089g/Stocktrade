// 화면 확인용 데모 데이터(npm run demo). 실제 기록이 아니며 화면 맨 위에 '데모 데이터'라고 표시한다.
// 엔진이 남기는 모양(runs.report, orders, positions)을 그대로 흉내 낸다.
import { formatPrice, kstDateKey, localDateKey, symbolName } from "../lib/format";
import type {
  Balance,
  Benchmark,
  EngineSettings,
  Market,
  Mode,
  Num,
  OrderRow,
  OrderStatus,
  PositionRow,
  ReportOrder,
  ReportOrderResult,
  RunReport,
  RunRow,
  SecretName,
  SecretStatus,
  Side,
  Signal,
  SkippedSignal,
  Snapshot,
  SwitchState,
} from "../lib/types";
import type { DataSource } from "./source";

const MODE: Mode = "paper";
const RUN_TIME: Record<Market, string> = { KR: "08:40", US: "23:40" };
const DAY = 86_400_000;

function at(now: Date, daysAgo: number, hhmm: string): string {
  const key = kstDateKey(new Date(now.getTime() - daysAgo * DAY));
  return new Date(`${key}T${hhmm}:00+09:00`).toISOString();
}

function isWeekend(iso: string): boolean {
  const day = new Date(`${kstDateKey(iso)}T00:00:00Z`).getUTCDay();
  return day === 0 || day === 6;
}

/** daysAgo가 주말이면 그 전 평일로(실행은 평일에만 있다) */
function tradingDay(now: Date, daysAgo: number, market: Market): number {
  let d = daysAgo;
  while (isWeekend(at(now, d, RUN_TIME[market]))) d += 1;
  return d;
}

// 결과가 늘 같도록 시드를 고정한 난수
function random(seed: number): () => number {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function walk(n: number, end: number, vol: number, seed: number, digits: number): number[] {
  const rnd = random(seed);
  const raw = [1];
  for (let i = 1; i < n; i++) raw.push((raw[i - 1] as number) * (1 + (rnd() - 0.48) * vol));
  const scale = end / (raw[n - 1] as number);
  return raw.map((v) => Number((v * scale).toFixed(digits)));
}

const PRICES: Record<Market, Record<string, number>> = {
  KR: { "005930": 73400, "000660": 192500, "005380": 251000, "035420": 210000, "105560": 98700 },
  US: { AAPL: 221.75, MSFT: 512.3, NVDA: 188.4, AMZN: 226.9, JPM: 301.1 },
};
const BALANCES: Record<Market, Balance> = {
  KR: { market: "KR", cash: "7942900", total_equity: "10153200" },
  US: { market: "US", cash: "7951.36", total_equity: "9968.76" },
};
const BENCH: Record<Market, { symbol: string; name: string; end: number; digits: number }> = {
  KR: { symbol: "069500", name: "KODEX 200", end: 61850, digits: 0 },
  US: { symbol: "SPY", name: "S&P500(SPY)", end: 672.35, digits: 2 },
};

interface OrderSpec {
  market: Market;
  daysAgo: number;
  symbol: string;
  strategy: string;
  side: Side;
  qty: number;
  limit: number;
  status: OrderStatus;
  fill?: number;
  reason: string;
  message?: string;
}

const ORDER_SPECS: OrderSpec[] = [
  { market: "KR", daysAgo: 40, symbol: "005380", strategy: "trend_ma", side: "BUY", qty: 4, limit: 264600, status: "FILLED", fill: 262000, reason: "골든크로스: 20일선 255,300, 60일선 254,900" },
  { market: "KR", daysAgo: 30, symbol: "005380", strategy: "trend_ma", side: "SELL", qty: 4, limit: 228400, status: "FILLED", fill: 238000, reason: "손절: 종가 235,500 ≤ 손절가 235,800" },
  { market: "KR", daysAgo: 25, symbol: "035420", strategy: "rsi_rebound", side: "BUY", qty: 6, limit: 203000, status: "FILLED", fill: 201000, reason: "RSI 과매도 탈출: 27.9 → 31.4" },
  { market: "KR", daysAgo: 18, symbol: "035420", strategy: "rsi_rebound", side: "SELL", qty: 6, limit: 203200, status: "FILLED", fill: 209500, reason: "RSI 회복: 52.3" },
  { market: "KR", daysAgo: 12, symbol: "005930", strategy: "trend_ma", side: "BUY", qty: 17, limit: 71200, status: "FILLED", fill: 70500, reason: "골든크로스: 20일선 69,880, 60일선 69,640" },
  { market: "KR", daysAgo: 3, symbol: "000660", strategy: "rsi_rebound", side: "BUY", qty: 5, limit: 199900, status: "FILLED", fill: 198000, reason: "RSI 과매도 탈출: 28.4 → 31.9" },
  { market: "KR", daysAgo: 0, symbol: "105560", strategy: "trend_ma", side: "BUY", qty: 12, limit: 99600, status: "SUBMITTED", reason: "골든크로스: 20일선 97,410, 60일선 97,180" },
  { market: "US", daysAgo: 35, symbol: "MSFT", strategy: "trend_ma", side: "BUY", qty: 2, limit: 502.98, status: "FILLED", fill: 498, reason: "골든크로스: 20일선 489.12, 60일선 488.40" },
  { market: "US", daysAgo: 22, symbol: "MSFT", strategy: "trend_ma", side: "SELL", qty: 2, limit: 503.62, status: "FILLED", fill: 519.4, reason: "데드크로스: 20일선 516.02, 60일선 516.88" },
  { market: "US", daysAgo: 20, symbol: "NVDA", strategy: "trend_ma", side: "BUY", qty: 6, limit: 183.01, status: "FILLED", fill: 181.2, reason: "골든크로스: 20일선 176.55, 60일선 176.10" },
  { market: "US", daysAgo: 5, symbol: "AMZN", strategy: "rsi_rebound", side: "BUY", qty: 3, limit: 231.2, status: "REJECTED", reason: "RSI 과매도 탈출: 29.1 → 30.6", message: "주문가능금액을 초과 했습니다" },
  { market: "US", daysAgo: 2, symbol: "AAPL", strategy: "rsi_rebound", side: "BUY", qty: 4, limit: 228.36, status: "FILLED", fill: 226.1, reason: "RSI 과매도 탈출: 26.8 → 33.0" },
];

const SIDE_KO: Record<Side, string> = { BUY: "매수", SELL: "매도" };
const STATUS_KO: Record<OrderStatus, string> = {
  SUBMITTED: "접수",
  PARTIALLY_FILLED: "부분체결",
  FILLED: "체결",
  REJECTED: "거부",
  CANCELLED: "취소",
  EXPIRED: "만료",
};

function label(symbol: string): string {
  const name = symbolName(symbol);
  return name === symbol ? symbol : `${name}(${symbol})`;
}

/** 엔진 알림과 같은 가격 표기(통화 기호 없이). 예: 70,500 / 181.20 */
function enginePrice(value: Num | null, market: Market): string {
  return formatPrice(value, market).replace(/원$|^\$/g, "");
}

function reportOrder(s: OrderSpec, cid: string): ReportOrder {
  return {
    market: s.market,
    symbol: s.symbol,
    side: s.side,
    qty: s.qty,
    order_type: "LIMIT",
    limit_price: String(s.limit),
    strategy: s.strategy,
    client_order_id: cid,
    reason: s.reason,
  };
}

function signalOf(s: OrderSpec, bar: string, reason = s.reason): Signal {
  return {
    market: s.market,
    symbol: s.symbol,
    side: s.side,
    strategy: s.strategy,
    price: String(s.limit),
    bar_date: bar,
    reason,
    stop_loss: reason.startsWith("손절"),
  };
}

/** 엔진의 format_report와 비슷한 알림 문장 */
function summaryOf(r: RunReport, started: string): string {
  const mode = r.mode === "paper" ? "모의투자" : "실전";
  const status = { OK: "완료", PAUSED: "일시정지", SKIPPED: "건너뜀", ERROR: "오류" }[r.status];
  let title = `[${mode}] ${r.market === "KR" ? "국내" : "미국"} · ${status}`;
  if (r.stop_loss_only) title += " · 손절 확인";
  const kst = new Date(new Date(started).getTime() + 9 * 3600_000).toISOString();
  const lines = [`${title} · ${kst.slice(0, 10)} ${kst.slice(11, 16)} KST`];
  if (r.message) lines.push(r.message);
  if (r.error) lines.push(`오류: ${r.error}`);
  if (r.fills?.length) {
    lines.push("새 체결");
    for (const f of r.fills)
      lines.push(` · ${SIDE_KO[f.order.side]} ${label(f.order.symbol)} ${f.qty}주 @ ${enginePrice(f.price, r.market)} [${f.order.strategy}]`);
  }
  if (r.orders?.length) {
    lines.push(`주문 ${r.orders.length}건`);
    for (const o of r.orders) {
      const status2 = STATUS_KO[o.status] + (o.message ? `: ${o.message}` : "");
      lines.push(` · ${SIDE_KO[o.order.side]} ${label(o.order.symbol)} ${o.order.qty}주 지정가 ${enginePrice(o.order.limit_price, r.market)} [${o.order.strategy}] → ${status2}`);
      if (o.order.reason) lines.push(`   (${o.order.reason})`);
    }
  }
  if (r.skipped?.length) {
    lines.push(`주문 안 함 ${r.skipped.length}건`);
    for (const s of r.skipped)
      lines.push(` · ${SIDE_KO[s.signal.side]} ${label(s.signal.symbol)} [${s.signal.strategy}]: ${s.reason}`);
  }
  if (r.warnings?.length) {
    lines.push("확인 필요");
    for (const w of r.warnings) lines.push(` · ${w}`);
  }
  if (r.status === "OK" && !r.signals?.length && !r.fills?.length)
    lines.push(r.stop_loss_only ? "손절할 종목이 없습니다." : "오늘은 신호가 없습니다.");
  return lines.join("\n");
}

function build(now: Date) {
  const dayOf = (s: OrderSpec) => tradingDay(now, s.daysAgo, s.market);
  const openedAt = (i: number) => {
    const spec = ORDER_SPECS[i] as OrderSpec;
    return at(now, dayOf(spec), RUN_TIME[spec.market]);
  };
  const orders: OrderRow[] = ORDER_SPECS.map((s, i) => {
    const created = at(now, dayOf(s), RUN_TIME[s.market]);
    const day = localDateKey(created, s.market);
    return {
      mode: MODE,
      client_order_id: `${day.replaceAll("-", "")}-${s.market}-${s.symbol}-${s.side}-${s.strategy}`,
      market: s.market,
      symbol: s.symbol,
      side: s.side,
      qty: s.qty,
      order_type: "LIMIT",
      limit_price: s.limit,
      strategy: s.strategy,
      trade_date: day,
      broker_order_id: String(36551 + i).padStart(10, "0"),
      status: s.status,
      filled_qty: s.fill ? s.qty : 0,
      avg_fill_price: s.fill ?? null,
      message: s.message ?? "",
      created_at: created,
      reason: s.reason,
    };
  });

  const positions: PositionRow[] = [
    { mode: MODE, market: "KR", symbol: "005930", strategy: "trend_ma", qty: 17, avg_price: 70500, opened_at: openedAt(4) },
    { mode: MODE, market: "KR", symbol: "000660", strategy: "rsi_rebound", qty: 5, avg_price: 198000, opened_at: openedAt(5) },
    { mode: MODE, market: "US", symbol: "NVDA", strategy: "trend_ma", qty: 6, avg_price: 181.2, opened_at: openedAt(9) },
    { mode: MODE, market: "US", symbol: "AAPL", strategy: "rsi_rebound", qty: 4, avg_price: 226.1, opened_at: openedAt(11) },
  ];

  const runs: RunRow[] = [];
  let id = 1;
  for (const market of ["KR", "US"] as Market[]) {
    // 미국 실행은 한국 시간 밤이라 오늘 것은 아직 없다고 본다
    const days: number[] = [];
    for (let d = 30; d >= (market === "KR" ? 0 : 1); d--)
      if (!isWeekend(at(now, d, RUN_TIME[market]))) days.push(d);
    const equity = walk(days.length, Number(BALANCES[market].total_equity), 0.012, market === "KR" ? 7 : 11, market === "KR" ? 0 : 2);
    const bench = walk(days.length, BENCH[market].end, 0.011, market === "KR" ? 3 : 5, BENCH[market].digits);
    days.forEach((d, i) => {
      const started = at(now, d, RUN_TIME[market]);
      const last = i === days.length - 1;
      const total = equity[i] as number;
      const balance: Balance = last
        ? BALANCES[market]
        : { market, cash: String(Math.round(total * 0.8)), total_equity: String(total) };
      const benchmark: Benchmark = {
        symbol: BENCH[market].symbol,
        name: BENCH[market].name,
        close: String(bench[i]),
        bar_date: kstDateKey(new Date(Date.parse(started) - DAY)),
      };
      const report: RunReport = {
        market,
        mode: MODE,
        started_at: started,
        finished_at: new Date(Date.parse(started) + 70_000).toISOString(),
        status: "OK",
        message: "",
        error: null,
        fills: [],
        signals: [],
        orders: [],
        skipped: [],
        warnings: [],
        balance,
        prices: last ? (PRICES[market] as Record<string, Num>) : {},
        benchmark,
      };
      for (const s of ORDER_SPECS.filter((o) => o.market === market && dayOf(o) === d)) {
        const cid = `${localDateKey(started, market).replaceAll("-", "")}-${market}-${s.symbol}-${s.side}-${s.strategy}`;
        const result: ReportOrderResult = {
          order: reportOrder(s, cid),
          broker_order_id: "0000036551",
          status: s.status === "FILLED" ? "SUBMITTED" : s.status,
          filled_qty: 0,
          avg_fill_price: null,
          message: s.message ?? "",
          created_at: started,
        };
        report.signals?.push(signalOf(s, benchmark.bar_date));
        report.orders?.push(result);
      }
      // 전날 낸 주문의 체결은 다음 실행에서 확인한다
      for (const s of ORDER_SPECS.filter((o) => o.market === market && o.fill && dayOf(o) > d && dayOf(o) - d <= 3)) {
        const prevFill = report.fills?.some((f) => f.order.symbol === s.symbol);
        const alreadyShown = runs.some((r) => r.market === market && r.report?.fills?.some((f) => f.order.symbol === s.symbol && f.order.side === s.side));
        if (!prevFill && !alreadyShown)
          report.fills?.push({ order: reportOrder(s, "x"), qty: s.qty, price: String(s.fill) });
      }
      runs.push({ id: id++, mode: MODE, market, status: "OK", message: "", error: null, started_at: started, finished_at: report.finished_at, summary: "", report });
    });
  }

  // 특별한 날: 주문 안 함, 일시정지, 오류, 손절 확인, 경고. 그날이 주말이면 그 전 가장 가까운 실행
  const find = (market: Market, daysAgo: number) => {
    const limit = Date.parse(at(now, daysAgo, "23:59"));
    return runs
      .filter((r) => r.market === market && Date.parse(r.started_at) <= limit)
      .sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at))[0];
  };
  const todayKr = find("KR", 0);
  if (todayKr?.report) {
    const skipped: SkippedSignal = {
      signal: signalOf({ ...ORDER_SPECS[5]!, strategy: "trend_ma", side: "BUY" }, "", "골든크로스: 20일선 193,100, 60일선 192,700"),
      reason: "금액 부족(종목당 한도 312,000)",
    };
    todayKr.report.skipped = [skipped];
    todayKr.report.signals?.push(skipped.signal);
  }
  const paused = find("KR", 8);
  if (paused?.report) {
    paused.status = paused.report.status = "PAUSED";
    paused.message = paused.report.message = "점검 중 (손절 매도만 냅니다)";
    const sig = signalOf({ market: "KR", daysAgo: 8, symbol: "035420", strategy: "trend_ma", side: "BUY", qty: 0, limit: 205000, status: "SUBMITTED", reason: "골든크로스: 20일선 204,400, 60일선 204,100" }, "");
    paused.report.signals = [sig];
    paused.report.skipped = [{ signal: sig, reason: "일시정지 중이라 주문하지 않았습니다(손절 매도만 나갑니다)" }];
  }
  const failed = find("US", 6);
  if (failed?.report) {
    failed.status = failed.report.status = "ERROR";
    failed.error = failed.report.error = "ReadTimeout: KIS 해외 잔고 조회가 응답하지 않습니다. 주문하지 않았습니다";
    failed.report.balance = null;
  }
  const warned = find("US", 1);
  if (warned?.report) warned.report.warnings = ["AAPL: 전략 기록 4주, 계좌 5주로 다릅니다"];

  const stopLoss: RunReport = {
    market: "KR",
    mode: MODE,
    started_at: at(now, 0, "13:17"),
    finished_at: at(now, 0, "13:18"),
    status: "OK",
    message: "",
    error: null,
    fills: [],
    signals: [],
    orders: [],
    skipped: [],
    warnings: [],
    stop_loss_only: true,
    balance: BALANCES.KR,
    prices: { "005930": 73100, "000660": 191800 },
    benchmark: null,
  };
  if (Date.parse(stopLoss.started_at) <= now.getTime())
    runs.push({ id: id++, mode: MODE, market: "KR", status: "OK", message: "", error: null, started_at: stopLoss.started_at, finished_at: stopLoss.finished_at, summary: "", report: stopLoss });

  for (const r of runs) if (r.report) r.summary = summaryOf(r.report, r.started_at);
  runs.sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at));
  return { orders, positions, runs };
}

export function createDemoSource(now: Date = new Date()): DataSource {
  const { orders, positions, runs } = build(now);
  let switchState: SwitchState = { paused: false, reason: "", updated_at: at(now, 9, "07:58") };
  let engineSettings: EngineSettings = {
    llm_provider: "anthropic",
    llm_model: "",
    llm_summary: true,
    llm_screen: "shadow",
    sec_user_agent: "",
    updated_at: at(now, 9, "07:50"),
  };
  const secrets = new Map<SecretName, SecretStatus>([
    ["llm_api_key", { name: "llm_api_key", hint: "x7Qa", updated_at: at(now, 9, "07:49") }],
  ]);
  const wait = <T>(value: T) => new Promise<T>((resolve) => setTimeout(() => resolve(value), 120));
  const byMode = <T extends { mode: Mode }>(rows: T[], mode: Mode) => rows.filter((r) => r.mode === mode);
  const withoutReport = (r: RunRow): RunRow => ({ ...r, report: undefined });

  return {
    demo: true,
    currentUser: () => wait({ id: "demo", email: "demo@example.com" }),
    onAuthChange: () => () => {},
    signIn: () => wait(undefined),
    signOut: () => wait(undefined),

    getSwitch: () => wait(switchState),
    setPause: (paused, reason) => {
      switchState = { paused, reason, updated_at: new Date().toISOString() };
      return wait(switchState);
    },

    getEngineSettings: () => wait(engineSettings),
    saveEngineSettings: (input) => {
      engineSettings = { ...input, updated_at: new Date().toISOString() };
      return wait(engineSettings);
    },
    secretStatus: () => wait([...secrets.values()]),
    setSecret: (name, value) => {
      // 데모는 끝 4자리만 기억한다
      if (value) secrets.set(name, { name, hint: value.slice(-4), updated_at: new Date().toISOString() });
      else secrets.delete(name);
      return wait(undefined);
    },

    latestMode: () => wait(MODE),
    listRuns: (mode, { limit, before }) =>
      wait(
        byMode(runs, mode)
          .filter((r) => !before || r.started_at < before)
          .slice(0, limit)
          .map(withoutReport),
      ),
    runsSince: (mode, since) => wait(byMode(runs, mode).filter((r) => r.started_at >= since)),
    getRun: (id) => wait(runs.find((r) => r.id === id) ?? null),
    latestSnapshots: (mode) => {
      const out: Partial<Record<Market, Snapshot>> = {};
      for (const r of byMode(runs, mode)) {
        const rep = r.report;
        if (!rep?.balance || out[r.market]) continue;
        out[r.market] = {
          runId: r.id,
          market: r.market,
          startedAt: r.started_at,
          stopLossOnly: rep.stop_loss_only === true,
          balance: rep.balance,
          prices: rep.prices ?? {},
        };
      }
      return wait(out);
    },
    equityHistory: (mode) =>
      wait(
        byMode(runs, mode)
          .filter((r) => (r.status === "OK" || r.status === "PAUSED") && !r.report?.stop_loss_only && r.report?.balance)
          .map((r) => ({
            market: r.market,
            startedAt: r.started_at,
            balance: r.report?.balance as Balance,
            benchmark: r.report?.benchmark ?? null,
          }))
          .reverse(),
      ),

    listPositions: (mode) => wait(byMode(positions, mode)),
    listOrders: (mode, { limit, before }) =>
      wait(
        byMode(orders, mode)
          .filter((o) => !before || o.created_at < before)
          .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
          .slice(0, limit),
      ),
    filledOrders: (mode) =>
      wait(
        byMode(orders, mode)
          .filter((o) => o.filled_qty > 0)
          .sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at)),
      ),

    savePushSubscription: () => wait(undefined),
    deletePushSubscription: () => wait(undefined),
  };
}
