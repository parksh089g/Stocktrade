import { describe, expect, it } from "vitest";
import { equityPoints, indexed, strategyStats } from "../src/lib/performance";
import type { PositionView } from "../src/lib/portfolio";
import type { EquityRow, OrderRow, Side } from "../src/lib/types";

let seq = 0;
function fill(
  symbol: string,
  strategy: string,
  side: Side,
  qty: number,
  price: number | null,
  at: string,
): OrderRow {
  seq += 1;
  return {
    mode: "paper",
    client_order_id: `c${seq}`,
    market: "KR",
    symbol,
    side,
    qty,
    order_type: "LIMIT",
    limit_price: price,
    strategy,
    trade_date: at.slice(0, 10),
    broker_order_id: `b${seq}`,
    status: "FILLED",
    filled_qty: qty,
    avg_fill_price: price,
    message: "",
    created_at: at,
  };
}

describe("전략별 성적표", () => {
  it("매수→매도를 묶어 실현손익, 승률, 평균 수익률을 셈한다", () => {
    const orders = [
      // 시간 순서가 섞여 와도 정렬해서 본다
      fill("035420", "rsi_rebound", "SELL", 6, 209_500, "2026-10-23T00:00:00Z"),
      fill("035420", "rsi_rebound", "BUY", 6, 201_000, "2026-10-16T00:00:00Z"),
      fill("005380", "rsi_rebound", "BUY", 4, 262_000, "2026-10-01T00:00:00Z"),
      fill("005380", "rsi_rebound", "SELL", 4, 238_000, "2026-10-10T00:00:00Z"),
    ];
    const [s] = strategyStats(orders, []);
    expect(s?.closedTrades).toBe(2);
    expect(s?.wins).toBe(1);
    expect(s?.winRate).toBe(0.5);
    expect(s?.realized).toBe(6 * 8_500 - 4 * 24_000);
    expect(s?.avgReturn).toBeCloseTo((8_500 / 201_000 + -24_000 / 262_000) / 2);
  });

  it("나눠 판 거래는 다 팔았을 때 한 건으로 센다", () => {
    const orders = [
      fill("A", "trend_ma", "BUY", 10, 100, "2026-10-01T00:00:00Z"),
      fill("A", "trend_ma", "SELL", 4, 110, "2026-10-02T00:00:00Z"),
      fill("A", "trend_ma", "SELL", 6, 90, "2026-10-03T00:00:00Z"),
    ];
    const [s] = strategyStats(orders, []);
    expect(s?.closedTrades).toBe(1);
    expect(s?.realized).toBe(40 - 60);
    expect(s?.wins).toBe(0);
    expect(s?.avgReturn).toBeCloseTo(-20 / 1000);
  });

  it("체결 가격을 모르는 주문과 매수 기록이 없는 매도는 건너뛴다", () => {
    const orders = [
      fill("A", "trend_ma", "SELL", 5, 100, "2026-10-01T00:00:00Z"),
      fill("B", "trend_ma", "BUY", 5, null, "2026-10-02T00:00:00Z"),
    ];
    expect(strategyStats(orders, [])).toEqual([]);
  });

  it("보유 중인 종목은 평가손익으로 따로 보여 준다", () => {
    const view = { market: "KR", strategy: "trend_ma", pnl: 49_300 } as PositionView;
    const unknown = { market: "KR", strategy: "trend_ma", pnl: null } as PositionView;
    const [s] = strategyStats([], [view, unknown]);
    expect(s?.openCount).toBe(2);
    expect(s?.unrealized).toBe(49_300);
    expect(s?.winRate).toBeNull();
  });
});

function row(market: "KR" | "US", at: string, equity: number, bench: number | null): EquityRow {
  return {
    market,
    startedAt: at,
    balance: { market, cash: "0", total_equity: String(equity) },
    benchmark:
      bench === null ? null : { symbol: "069500", name: "KODEX 200", close: String(bench), bar_date: "" },
  };
}

describe("수익 곡선", () => {
  it("하루 한 점(그날 첫 매매 실행), 날짜 순서", () => {
    const pts = equityPoints(
      [
        row("KR", "2026-11-10T00:47:00Z", 101, 11), // 같은 날 백업 실행
        row("KR", "2026-11-09T23:40:00Z", 100, 10), // 11/10 08:40 KST
        row("KR", "2026-11-08T23:40:00Z", 99, 9),
        row("US", "2026-11-09T14:40:00Z", 5, 5),
      ],
      "KR",
    );
    expect(pts).toEqual([
      { date: "2026-11-09", equity: 99, bench: 9 },
      { date: "2026-11-10", equity: 100, bench: 10 },
    ]);
  });

  it("계좌와 지수가 모두 있는 첫날을 100으로 맞춘다", () => {
    const pts = [
      { date: "d1", equity: 100, bench: null },
      { date: "d2", equity: 200, bench: 50 },
      { date: "d3", equity: 220, bench: 55 },
      { date: "d4", equity: 180, bench: null },
    ];
    const got = indexed(pts);
    expect(got.map((p) => p.date)).toEqual(["d2", "d3", "d4"]);
    expect(got.map((p) => p.account)).toEqual([100, expect.closeTo(110), 90]);
    expect(got.map((p) => p.bench)).toEqual([100, expect.closeTo(110), null]);
  });

  it("지수가 하루도 없으면 계좌만 첫날 기준", () => {
    expect(indexed([{ date: "d1", equity: 50, bench: null }])).toEqual([
      { date: "d1", account: 100, bench: null },
    ]);
    expect(indexed([])).toEqual([]);
  });
});
