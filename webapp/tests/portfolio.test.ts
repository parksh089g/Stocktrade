import { describe, expect, it } from "vitest";
import { allocation, buyReasons, positionViews } from "../src/lib/portfolio";
import type { Balance, OrderRow, PositionRow } from "../src/lib/types";

const NOW = new Date("2026-11-10T05:00:00Z");

function pos(symbol: string, strategy: string, qty: number, avg: number): PositionRow {
  return {
    mode: "paper",
    market: "KR",
    symbol,
    strategy,
    qty,
    avg_price: avg,
    opened_at: "2026-10-29T23:40:00Z",
  };
}

function buy(symbol: string, strategy: string, reason: string, at: string): OrderRow {
  return {
    mode: "paper",
    client_order_id: `${at}-${symbol}-${strategy}`,
    market: "KR",
    symbol,
    side: "BUY",
    qty: 1,
    order_type: "LIMIT",
    limit_price: 1,
    strategy,
    trade_date: at.slice(0, 10),
    broker_order_id: "1",
    status: "FILLED",
    filled_qty: 1,
    avg_fill_price: 1,
    message: "",
    created_at: at,
    reason,
  };
}

const BALANCE: Balance = { market: "KR", cash: "7942900", total_equity: "10153200" };
const PRICES = { KR: { "005930": "73400.0", "000660": "192500.0" } };

describe("보유 종목 평가", () => {
  it("마지막 실행 가격으로 평가손익을 셈하고 산 이유를 붙인다", () => {
    const reasons = buyReasons([
      buy("005930", "trend_ma", "옛 근거", "2026-09-01T00:00:00Z"),
      buy("005930", "trend_ma", "골든크로스: 20일선 69,880, 60일선 69,640", "2026-10-29T23:40:00Z"),
      buy("005930", "rsi_rebound", "다른 전략", "2026-10-30T00:00:00Z"),
    ]);
    const [v] = positionViews([pos("005930", "trend_ma", 17, 70500)], PRICES, reasons, NOW);
    expect(v?.price).toBe(73400);
    expect(v?.pnl).toBe(17 * (73400 - 70500));
    expect(v?.pnlRatio).toBeCloseTo(73400 / 70500 - 1);
    expect(v?.heldDays).toBe(11);
    expect(v?.reason).toBe("골든크로스: 20일선 69,880, 60일선 69,640");
  });

  it("가격을 모르면 평가손익을 지어내지 않는다", () => {
    const [v] = positionViews([pos("035420", "trend_ma", 3, 200000)], PRICES, new Map(), NOW);
    expect(v?.price).toBeNull();
    expect(v?.pnl).toBeNull();
    expect(v?.reason).toBe("");
  });
});

describe("전략별 비중", () => {
  const views = positionViews(
    [pos("005930", "trend_ma", 17, 70500), pos("000660", "rsi_rebound", 5, 198000)],
    PRICES,
    new Map(),
    NOW,
  );

  it("[추세, 반등, 현금, 기타]로 나눈다", () => {
    const a = allocation(BALANCE, views);
    expect(a.parts.map((p) => p.key)).toEqual(["trend_ma", "rsi_rebound", "cash", "other"]);
    expect(a.parts.map((p) => p.value)).toEqual([1_247_800, 962_500, 7_942_900, 0]);
    expect(a.parts[0]?.share).toBeCloseTo(1_247_800 / 10_153_200);
    expect(a.parts[0]?.target).toBe(0.5);
    expect(a.parts[2]?.target).toBeNull();
    expect(a.total).toBe(10_153_200);
    expect(a.pnl).toBe(17 * 2900 + 5 * -5500);
    expect(a.approximate).toBe(false);
  });

  it("총평가에서 남는 금액은 기타(주문 대기 금액 등)", () => {
    const a = allocation({ ...BALANCE, total_equity: "10253200" }, views);
    expect(a.parts[3]?.value).toBe(100_000);
  });

  it("아주 작은 차이(총평가의 0.5% 미만)는 기타로 그리지 않는다", () => {
    const a = allocation({ ...BALANCE, total_equity: "10161800" }, views); // 8,600원 차이
    expect(a.parts[3]?.value).toBe(0);
  });

  it("기타가 음수면(평가 시점 차이) 0으로 두고 그리는 부분들의 합으로 나눈다", () => {
    const a = allocation({ ...BALANCE, total_equity: "10000000" }, views);
    expect(a.parts[3]?.value).toBe(0);
    const sum = 1_247_800 + 962_500 + 7_942_900;
    expect(a.parts[2]?.share).toBeCloseTo(7_942_900 / sum);
  });

  it("가격을 모르는 종목은 평단으로 셈하고 표시한다", () => {
    const more = positionViews([pos("035420", "trend_ma", 3, 200000)], PRICES, new Map(), NOW);
    const a = allocation(BALANCE, [...views, ...more]);
    expect(a.approximate).toBe(true);
    expect(a.parts[0]?.value).toBe(1_247_800 + 600_000);
  });

  it("다른 시장의 보유분은 넣지 않는다", () => {
    const a = allocation({ market: "US", cash: "100", total_equity: "100" }, views);
    expect(a.parts.map((p) => p.value)).toEqual([0, 0, 100, 0]);
  });
});
