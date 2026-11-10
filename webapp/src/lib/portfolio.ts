// 보유 종목 평가와 전략별 비중. 가격은 엔진이 마지막 실행에서 쓴 값(웹앱은 시세를 따로 받지 않는다).
import { kstDaysBetween, toNum } from "./format";
import type { Balance, Market, Num, OrderRow, PositionRow } from "./types";

/** 엔진의 전략별 자금 배분(trading/risk/config.py RiskConfig.strategy_weights) */
export const STRATEGY_TARGETS: Record<string, number> = { trend_ma: 0.5, rsi_rebound: 0.5 };
export const STRATEGY_ORDER = ["trend_ma", "rsi_rebound"];
// 총평가 - 현금 - 전략 평가액이 총평가의 이 비율보다 작으면 기타로 보지 않는다
const OTHER_MIN_SHARE = 0.005;

export interface PositionView {
  market: Market;
  symbol: string;
  strategy: string;
  qty: number;
  avgPrice: number;
  price: number | null; // 마지막 실행의 가격. 없으면 null(지어내지 않는다)
  cost: number;
  value: number | null;
  pnl: number | null;
  pnlRatio: number | null;
  openedAt: string;
  heldDays: number;
  reason: string; // 산 이유(그 전략의 마지막 매수 주문 근거). 모르면 ""
}

/** 종목·전략별로 가장 최근 매수 주문의 근거 */
export function buyReasons(orders: OrderRow[]): Map<string, string> {
  const latest = new Map<string, OrderRow>();
  for (const o of orders) {
    if (o.side !== "BUY" || !o.reason) continue;
    const key = positionKey(o.market, o.symbol, o.strategy);
    const seen = latest.get(key);
    if (!seen || Date.parse(seen.created_at) < Date.parse(o.created_at)) latest.set(key, o);
  }
  return new Map([...latest].map(([k, o]) => [k, o.reason ?? ""]));
}

export function positionKey(market: Market, symbol: string, strategy: string): string {
  return `${market}|${symbol}|${strategy}`;
}

export function positionViews(
  positions: PositionRow[],
  prices: Partial<Record<Market, Record<string, Num>>>,
  reasons: Map<string, string>,
  now: Date = new Date(),
): PositionView[] {
  return positions.map((p) => {
    const avgPrice = toNum(p.avg_price) ?? 0;
    const price = toNum(prices[p.market]?.[p.symbol]);
    const cost = avgPrice * p.qty;
    const value = price === null ? null : price * p.qty;
    return {
      market: p.market,
      symbol: p.symbol,
      strategy: p.strategy,
      qty: p.qty,
      avgPrice,
      price,
      cost,
      value,
      pnl: value === null ? null : value - cost,
      pnlRatio: price === null || avgPrice === 0 ? null : price / avgPrice - 1,
      openedAt: p.opened_at,
      heldDays: kstDaysBetween(p.opened_at, now),
      reason: reasons.get(positionKey(p.market, p.symbol, p.strategy)) ?? "",
    };
  });
}

export interface AllocationPart {
  key: string; // trend_ma | rsi_rebound | cash | other
  value: number;
  share: number;
  target: number | null;
}

export interface Allocation {
  total: number;
  parts: AllocationPart[];
  approximate: boolean; // 가격을 몰라 평단으로 셈한 종목이 있다
  pnl: number; // 가격을 아는 종목의 평가손익 합
  cost: number; // 그 종목들의 매수 금액 합
}

/**
 * 시장 하나의 비중: [전략별 평가액 …, 현금, 기타].
 * 기타 = 총평가 - 현금 - 전략 평가액(주문 대기 금액, 직접 산 종목, 평가 시점 차이 등).
 * 기타가 음수면(시점 차이) 0으로 두고, 비중은 막대에 그리는 부분들의 합으로 나눈다.
 */
export function allocation(balance: Balance, views: PositionView[]): Allocation {
  const total = toNum(balance.total_equity) ?? 0;
  const cash = toNum(balance.cash) ?? 0;
  const byStrategy = new Map<string, number>(STRATEGY_ORDER.map((s) => [s, 0]));
  let approximate = false;
  let pnl = 0;
  let cost = 0;
  for (const v of views) {
    if (v.market !== balance.market) continue;
    const value = v.value ?? v.cost;
    if (v.value === null) approximate = true;
    else {
      pnl += v.pnl ?? 0;
      cost += v.cost;
    }
    byStrategy.set(v.strategy, (byStrategy.get(v.strategy) ?? 0) + value);
  }
  const invested = [...byStrategy.values()].reduce((a, b) => a + b, 0);
  const rest = total - cash - invested;
  // 아주 작은 차이(평가 시점, 소수점 오차)는 기타로 그리지 않는다
  const other = rest >= total * OTHER_MIN_SHARE ? rest : 0;
  const raw: Omit<AllocationPart, "share">[] = [
    ...[...byStrategy].map(([key, value]) => ({
      key,
      value,
      target: STRATEGY_TARGETS[key] ?? null,
    })),
    { key: "cash", value: cash, target: null },
    { key: "other", value: other, target: null },
  ];
  const sum = raw.reduce((a, p) => a + p.value, 0);
  return {
    total,
    parts: raw.map((p) => ({ ...p, share: sum > 0 ? p.value / sum : 0 })),
    approximate,
    pnl,
    cost,
  };
}
