// Supabase 표(supabase/schema.sql)와 엔진 실행 기록(trading/engine/report.py RunReport)의 모양.
// 표의 numeric 칸은 JSON 숫자로, 실행 기록(report jsonb) 안의 금액은 문자열(Decimal)로 온다.
// 그래서 금액 칸은 number | string으로 두고 lib/format.ts의 toNum으로 읽는다.

export type Market = "KR" | "US";
export type Mode = "paper" | "live";
export type Side = "BUY" | "SELL";
export type OrderStatus =
  | "SUBMITTED"
  | "PARTIALLY_FILLED"
  | "FILLED"
  | "REJECTED"
  | "CANCELLED"
  | "EXPIRED";
export type RunStatus = "OK" | "PAUSED" | "SKIPPED" | "ERROR";

export type Num = number | string;

export const MARKETS: readonly Market[] = ["KR", "US"];

export interface SwitchState {
  paused: boolean;
  reason: string;
  updated_at: string;
}

export interface PositionRow {
  mode: Mode;
  market: Market;
  symbol: string;
  strategy: string;
  qty: number;
  avg_price: Num;
  opened_at: string;
  updated_at?: string;
}

export interface OrderRow {
  mode: Mode;
  client_order_id: string;
  market: Market;
  symbol: string;
  side: Side;
  qty: number;
  order_type: "MARKET" | "LIMIT";
  limit_price: Num | null;
  strategy: string;
  trade_date: string;
  broker_order_id: string | null;
  status: OrderStatus;
  filled_qty: number;
  avg_fill_price: Num | null;
  message: string;
  created_at: string;
  reason?: string;
}

// ---- 실행 기록(runs.report) ----

export interface ReportOrder {
  market: Market;
  symbol: string;
  side: Side;
  qty: number;
  order_type: "MARKET" | "LIMIT";
  limit_price: Num | null;
  strategy: string;
  client_order_id: string;
  reason?: string;
}

export interface ReportOrderResult {
  order: ReportOrder;
  broker_order_id: string | null;
  status: OrderStatus;
  filled_qty: number;
  avg_fill_price: Num | null;
  message: string;
  created_at: string;
}

export interface Signal {
  market: Market;
  symbol: string;
  side: Side;
  strategy: string;
  price: Num;
  bar_date: string;
  reason: string;
  stop_loss: boolean;
}

export interface SkippedSignal {
  signal: Signal;
  reason: string;
}

export interface FillUpdate {
  order: ReportOrder;
  qty: number;
  price: Num;
}

export interface Balance {
  market: Market;
  cash: Num;
  total_equity: Num;
}

export interface Benchmark {
  symbol: string;
  name: string;
  close: Num;
  bar_date: string;
}

export interface RunReport {
  market: Market;
  mode: Mode;
  started_at: string;
  finished_at: string | null;
  status: RunStatus;
  message: string;
  error: string | null;
  fills?: FillUpdate[];
  signals?: Signal[];
  orders?: ReportOrderResult[];
  skipped?: SkippedSignal[];
  warnings?: string[];
  dry_run?: boolean;
  planned?: ReportOrder[];
  stop_loss_only?: boolean;
  day_over?: boolean;
  // 2026-11-10부터 남기는 웹앱용 기록. 그 전 실행에는 없다
  balance?: Balance | null;
  prices?: Record<string, Num>;
  benchmark?: Benchmark | null;
}

export interface RunRow {
  id: number;
  mode: Mode;
  market: Market;
  status: RunStatus;
  message: string;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  summary: string;
  report?: RunReport;
}

/** 시장별 가장 최근의 잔고 기록(비중, 평가손익의 기준). */
export interface Snapshot {
  runId: number;
  market: Market;
  startedAt: string;
  stopLossOnly: boolean;
  balance: Balance;
  prices: Record<string, Num>;
}

/** 수익 곡선용: 매매 실행 하나의 잔고와 비교 지수 종가. */
export interface EquityRow {
  market: Market;
  startedAt: string;
  balance: Balance;
  benchmark: Benchmark | null;
}

// ---- 엔진 설정(웹앱 설정 화면, engine_settings 표와 Supabase Vault) ----

export type LlmProvider = "none" | "anthropic";
export type LlmScreen = "off" | "shadow";

export interface EngineSettings {
  llm_provider: LlmProvider;
  llm_model: string; // 비우면 엔진 기본값(claude-opus-5-5)
  llm_summary: boolean;
  llm_screen: LlmScreen;
  sec_user_agent: string;
  updated_at: string;
}

export type EngineSettingsInput = Omit<EngineSettings, "updated_at">;

/** 웹앱에서 넣는 비밀값 이름. 값은 Vault에 암호화돼 있고 웹앱은 다시 읽지 못한다. */
export type SecretName = "llm_api_key" | "dart_api_key";

/** 넣어 둔 비밀값의 끝 4자리와 바꾼 시각. 없는 이름은 목록에 없다. */
export interface SecretStatus {
  name: SecretName;
  hint: string;
  updated_at: string;
}
