// 화면 표기. 시간은 UTC로 저장돼 있고 화면에는 한국 시간으로 보여 준다(미국 주문 날짜만 뉴욕 날짜).
import type { Market, Num } from "./types";

export const KST = "Asia/Seoul";
const NEW_YORK = "America/New_York";
const MINUS = "−";
const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

/** 금액 문자열(Decimal)이나 숫자를 숫자로. 비었거나 이상하면 null. */
export function toNum(value: Num | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

interface Parts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  weekday: number;
}

function partsIn(iso: string | Date, timeZone: string): Parts {
  const date = typeof iso === "string" ? new Date(iso) : iso;
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    hourCycle: "h23",
    weekday: "short",
  });
  const got: Record<string, string> = {};
  for (const p of fmt.formatToParts(date)) got[p.type] = p.value;
  const weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(got.weekday ?? "");
  return {
    year: Number(got.year),
    month: Number(got.month),
    day: Number(got.day),
    hour: Number(got.hour) % 24,
    minute: Number(got.minute),
    weekday,
  };
}

const pad = (n: number) => String(n).padStart(2, "0");

/** 예: 11/10(화) 08:41 */
export function formatKst(iso: string | Date): string {
  const p = partsIn(iso, KST);
  return `${p.month}/${p.day}(${WEEKDAYS[p.weekday]}) ${pad(p.hour)}:${pad(p.minute)}`;
}

/** 예: 11/10(화) */
export function formatKstDay(iso: string | Date): string {
  const p = partsIn(iso, KST);
  return `${p.month}/${p.day}(${WEEKDAYS[p.weekday]})`;
}

/** 한국 날짜 YYYY-MM-DD */
export function kstDateKey(iso: string | Date): string {
  const p = partsIn(iso, KST);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}`;
}

/** 그 시장 현지 날짜 YYYY-MM-DD (엔진의 trading_date와 같은 기준) */
export function localDateKey(iso: string | Date, market: Market): string {
  const p = partsIn(iso, market === "KR" ? KST : NEW_YORK);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}`;
}

/** YYYY-MM-DD → 11/10(화) (날짜만 있는 값, 시간대 변환 없음) */
export function formatDateKey(key: string): string {
  const [y, m, d] = key.split("-").map(Number);
  const weekday = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1)).getUTCDay();
  return `${m}/${d}(${WEEKDAYS[weekday]})`;
}

/** 한국 시간 오늘 0시의 UTC ISO 문자열 */
export function kstMidnightIso(now: Date = new Date()): string {
  const key = kstDateKey(now);
  return new Date(`${key}T00:00:00+09:00`).toISOString();
}

/** 두 시각 사이의 한국 날짜 차이(일). 보유 일수 표시에 쓴다. */
export function kstDaysBetween(fromIso: string, to: Date = new Date()): number {
  const a = Date.parse(`${kstDateKey(fromIso)}T00:00:00Z`);
  const b = Date.parse(`${kstDateKey(to)}T00:00:00Z`);
  return Math.round((b - a) / 86_400_000);
}

function grouped(value: number, digits: number): string {
  return value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function sign(value: number, signed: boolean): string {
  if (value < 0) return MINUS;
  return signed && value > 0 ? "+" : "";
}

/** 주가 표기. 예: 70,000원 / $180.50 */
export function formatPrice(value: Num | null | undefined, market: Market): string {
  const n = toNum(value);
  if (n === null) return "—";
  return market === "KR" ? `${grouped(n, 0)}원` : `$${grouped(n, 2)}`;
}

/**
 * 금액 표기. compact면 원화 100만원 이상은 만/억 단위(1,035만원, 1억 2,345만원), 달러는 센트를 뺀다.
 * 100만원 아래는 줄이면 뭉개지므로(12,800원 → 1만원) 그대로 쓴다. signed면 양수에 +를 붙인다.
 */
export function formatMoney(
  value: Num | null | undefined,
  market: Market,
  opts: { compact?: boolean; signed?: boolean } = {},
): string {
  const n = toNum(value);
  if (n === null) return "—";
  const s = sign(n, opts.signed ?? false);
  const abs = Math.abs(n);
  if (market === "US") return `${s}$${grouped(abs, opts.compact ? 0 : 2)}`;
  if (!opts.compact || abs < 1_000_000) return `${s}${grouped(abs, 0)}원`;
  if (abs < 100_000_000) return `${s}${grouped(Math.round(abs / 10_000), 0)}만원`;
  const eok = Math.floor(abs / 100_000_000);
  const man = Math.round((abs % 100_000_000) / 10_000);
  return `${s}${grouped(eok, 0)}억${man ? ` ${grouped(man, 0)}만` : ""}원`;
}

/** 비율 표기. 0.032 → 3.2% (signed면 +3.2%) */
export function formatPercent(
  ratio: number | null | undefined,
  opts: { signed?: boolean; digits?: number } = {},
): string {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return "—";
  const pct = ratio * 100;
  const digits = opts.digits ?? 1;
  const rounded = Number(pct.toFixed(digits));
  return `${sign(rounded, opts.signed ?? false)}${grouped(Math.abs(rounded), digits)}%`;
}

export function formatQty(qty: number): string {
  return `${grouped(qty, 0)}주`;
}

/** 오르면 up(빨강), 내리면 down(파랑). 한국 증권 앱과 같은 색 규칙 */
export function polarity(value: number | null | undefined): "up" | "down" | "flat" {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}

export function marketLabel(market: Market): string {
  return market === "KR" ? "국내" : "미국";
}

const STRATEGY_LABELS: Record<string, { short: string; full: string }> = {
  trend_ma: { short: "추세", full: "추세 추종" },
  rsi_rebound: { short: "반등", full: "단기 반등" },
};

export function strategyLabel(strategy: string, full = false): string {
  const label = STRATEGY_LABELS[strategy];
  if (!label) return strategy;
  return full ? label.full : label.short;
}

const NAMES: Record<string, string> =
  typeof __SYMBOL_NAMES__ === "undefined" ? {} : __SYMBOL_NAMES__;

/** 종목 이름(목록에 없으면 종목 코드만). 예: 삼성전자 */
export function symbolName(symbol: string): string {
  return NAMES[symbol] ?? symbol;
}

export function sideLabel(side: "BUY" | "SELL"): string {
  return side === "BUY" ? "매수" : "매도";
}
