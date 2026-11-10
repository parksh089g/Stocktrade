import { describe, expect, it } from "vitest";
import { latestByMarket, orderReason, runBody, runCounts, runTitle } from "../src/lib/runs";
import type { ReportOrder, Signal } from "../src/lib/types";

const SUMMARY = "[모의투자] 국내 · 완료 · 2026-11-10 08:40 KST\n주문 1건\n · 매수 삼성전자(005930) 17주";

describe("실행 기록", () => {
  it("summary 첫 줄은 제목, 나머지는 본문", () => {
    expect(runTitle({ summary: SUMMARY })).toBe("[모의투자] 국내 · 완료 · 2026-11-10 08:40 KST");
    expect(runBody({ summary: SUMMARY })).toBe("주문 1건\n · 매수 삼성전자(005930) 17주");
    expect(runBody({ summary: "제목만" })).toBe("");
  });

  it("옛 기록처럼 칸이 없어도 센다", () => {
    expect(runCounts(undefined)).toEqual({ fills: 0, orders: 0, planned: 0, skipped: 0, warnings: 0 });
  });

  it("주문 근거: 주문 기록의 이유, 없으면 같은 종목·전략의 신호 근거", () => {
    const order = { symbol: "005930", strategy: "trend_ma" } as ReportOrder;
    const signals = [
      { symbol: "005930", strategy: "rsi_rebound", reason: "다른 전략" },
      { symbol: "005930", strategy: "trend_ma", reason: "골든크로스" },
    ] as Signal[];
    expect(orderReason(order, signals)).toBe("골든크로스");
    expect(orderReason({ ...order, reason: "주문에 남은 이유" }, signals)).toBe("주문에 남은 이유");
    expect(orderReason(order, undefined)).toBe("");
  });

  it("시장별 가장 최근 실행", () => {
    const runs: { id: number; market: "KR" | "US" }[] = [
      { id: 3, market: "US" },
      { id: 2, market: "KR" },
      { id: 1, market: "US" },
    ];
    const latest = latestByMarket(runs);
    expect(latest.get("US")?.id).toBe(3);
    expect(latest.get("KR")?.id).toBe(2);
  });
});
