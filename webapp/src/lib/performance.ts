// 성적: 전략별 성적표(체결된 주문으로 계산)와 지수 대비 수익 곡선(실행 기록의 잔고로 계산).
import { localDateKey, toNum } from "./format";
import type { EquityRow, Market, OrderRow } from "./types";
import type { PositionView } from "./portfolio";

export interface StrategyStats {
  market: Market;
  strategy: string;
  closedTrades: number;
  wins: number;
  winRate: number | null;
  avgReturn: number | null; // 끝난 거래 수익률의 평균
  realized: number; // 실현손익 합(수수료·세금 제외)
  openCount: number;
  unrealized: number | null; // 보유 중 평가손익(가격을 아는 종목만)
}

interface Lot {
  qty: number;
  cost: number; // 남은 수량의 매수 금액
  tradeCost: number; // 이번 거래(보유 시작부터 다 팔 때까지)에서 판 수량의 매수 금액
  tradePnl: number;
}

/**
 * 체결된 주문을 시간 순서로 따라가며 종목·전략별로 매수→매도를 묶는다.
 * 평균단가 기준으로 실현손익을 셈하고, 수량이 0이 되면 거래 하나가 끝난 것으로 본다.
 * 체결 가격을 모르는 주문은 건너뛴다.
 */
export function strategyStats(orders: OrderRow[], views: PositionView[]): StrategyStats[] {
  const filled = orders
    .filter((o) => o.filled_qty > 0 && toNum(o.avg_fill_price) !== null)
    .sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));

  const lots = new Map<string, Lot>();
  const stats = new Map<string, StrategyStats>();
  const statFor = (market: Market, strategy: string): StrategyStats => {
    const key = `${market}|${strategy}`;
    let s = stats.get(key);
    if (!s) {
      s = {
        market,
        strategy,
        closedTrades: 0,
        wins: 0,
        winRate: null,
        avgReturn: null,
        realized: 0,
        openCount: 0,
        unrealized: null,
      };
      stats.set(key, s);
    }
    return s;
  };
  const returns = new Map<string, number[]>();

  for (const o of filled) {
    const key = `${o.market}|${o.symbol}|${o.strategy}`;
    const price = toNum(o.avg_fill_price) as number;
    const lot = lots.get(key) ?? { qty: 0, cost: 0, tradeCost: 0, tradePnl: 0 };
    lots.set(key, lot);
    if (o.side === "BUY") {
      statFor(o.market, o.strategy);
      lot.qty += o.filled_qty;
      lot.cost += o.filled_qty * price;
      continue;
    }
    const qty = Math.min(o.filled_qty, lot.qty);
    if (qty <= 0) continue; // 매수 기록이 없는 매도(기록 시작 전 보유분)
    const s = statFor(o.market, o.strategy);
    const avg = lot.cost / lot.qty;
    const pnl = qty * (price - avg);
    s.realized += pnl;
    lot.tradePnl += pnl;
    lot.tradeCost += qty * avg;
    lot.cost -= qty * avg;
    lot.qty -= qty;
    if (lot.qty === 0) {
      s.closedTrades += 1;
      if (lot.tradePnl > 0) s.wins += 1;
      const key2 = `${o.market}|${o.strategy}`;
      returns.set(key2, [...(returns.get(key2) ?? []), lot.tradePnl / lot.tradeCost]);
      lot.cost = 0;
      lot.tradeCost = 0;
      lot.tradePnl = 0;
    }
  }

  for (const v of views) {
    const s = statFor(v.market, v.strategy);
    s.openCount += 1;
    if (v.pnl !== null) s.unrealized = (s.unrealized ?? 0) + v.pnl;
  }
  for (const s of stats.values()) {
    if (s.closedTrades > 0) {
      s.winRate = s.wins / s.closedTrades;
      const r = returns.get(`${s.market}|${s.strategy}`) ?? [];
      s.avgReturn = r.reduce((a, b) => a + b, 0) / r.length;
    }
  }
  return [...stats.values()];
}

export interface EquityPoint {
  date: string; // 그 시장 현지 날짜
  equity: number;
  bench: number | null;
}

/** 시장 하나의 하루 한 점(그날 첫 매매 실행의 총평가와 비교 지수 종가), 날짜 순서 */
export function equityPoints(rows: EquityRow[], market: Market): EquityPoint[] {
  const byDay = new Map<string, EquityRow>();
  for (const r of rows) {
    if (r.market !== market) continue;
    const day = localDateKey(r.startedAt, market);
    const seen = byDay.get(day);
    if (!seen || Date.parse(r.startedAt) < Date.parse(seen.startedAt)) byDay.set(day, r);
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .flatMap(([date, r]) => {
      const equity = toNum(r.balance.total_equity);
      if (equity === null || equity <= 0) return [];
      return [{ date, equity, bench: toNum(r.benchmark?.close) }];
    });
}

export interface IndexedPoint {
  date: string;
  account: number;
  bench: number | null;
}

/**
 * 두 곡선을 같은 날 100으로 맞춘다(축 하나로 비교). 시작일은 계좌와 지수가 모두 있는 첫날.
 * 지수가 하루도 없으면 계좌만 첫날 기준으로 맞춘다.
 */
export function indexed(points: EquityPoint[]): IndexedPoint[] {
  const start = points.findIndex((p) => p.bench !== null);
  const from = start === -1 ? 0 : start;
  const base = points[from];
  if (!base) return [];
  return points.slice(from).map((p) => ({
    date: p.date,
    account: (p.equity / base.equity) * 100,
    bench: p.bench !== null && base.bench !== null ? (p.bench / base.bench) * 100 : null,
  }));
}
