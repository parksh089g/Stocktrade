import { describe, expect, it } from "vitest";
import {
  formatDateKey,
  formatKst,
  formatMoney,
  formatPercent,
  formatPrice,
  kstDateKey,
  kstDaysBetween,
  kstMidnightIso,
  localDateKey,
  polarity,
  toNum,
} from "../src/lib/format";

describe("시간: 저장은 UTC, 화면은 한국 시간", () => {
  it("한국 시간으로 보여 준다", () => {
    expect(formatKst("2026-11-09T23:41:00Z")).toBe("11/10(화) 08:41");
  });

  it("한국 자정을 넘기면 다음 날이다", () => {
    expect(kstDateKey("2026-11-10T14:59:00Z")).toBe("2026-11-10");
    expect(kstDateKey("2026-11-10T15:30:00Z")).toBe("2026-11-11");
  });

  it("미국 날짜는 뉴욕 기준(한국 23:40 실행은 뉴욕 같은 날 아침)", () => {
    expect(localDateKey("2026-11-10T14:40:00Z", "US")).toBe("2026-11-10");
    // 한국 화 02:47 손절 확인 = 뉴욕 월 12:47
    expect(localDateKey("2026-11-10T17:47:00Z", "US")).toBe("2026-11-10");
    expect(localDateKey("2026-11-10T17:47:00Z", "KR")).toBe("2026-11-11");
  });

  it("오늘 0시(한국)를 UTC로", () => {
    expect(kstMidnightIso(new Date("2026-11-10T05:00:00Z"))).toBe("2026-11-09T15:00:00.000Z");
  });

  it("보유 일수는 한국 날짜로 센다", () => {
    expect(kstDaysBetween("2026-10-29T23:40:00Z", new Date("2026-11-10T05:00:00Z"))).toBe(11);
  });

  it("날짜만 있는 값", () => {
    expect(formatDateKey("2026-11-10")).toBe("11/10(화)");
  });
});

describe("금액과 가격", () => {
  it("문자열 금액(Decimal)을 숫자로", () => {
    expect(toNum("70000.0")).toBe(70000);
    expect(toNum(180.5)).toBe(180.5);
    expect(toNum("")).toBeNull();
    expect(toNum("abc")).toBeNull();
    expect(toNum(null)).toBeNull();
  });

  it("주가", () => {
    expect(formatPrice(70000, "KR")).toBe("70,000원");
    expect(formatPrice("180.5", "US")).toBe("$180.50");
    expect(formatPrice(null, "KR")).toBe("—");
  });

  it("원화는 만/억 단위로 줄인다", () => {
    expect(formatMoney(10_350_000, "KR", { compact: true })).toBe("1,035만원");
    expect(formatMoney(123_450_000, "KR", { compact: true })).toBe("1억 2,345만원");
    expect(formatMoney(100_000_000, "KR", { compact: true })).toBe("1억원");
    expect(formatMoney(9_999, "KR", { compact: true })).toBe("9,999원");
    // 100만원 아래는 줄이지 않는다(손익이 1만원으로 뭉개지지 않게)
    expect(formatMoney(-96_000, "KR", { compact: true, signed: true })).toBe("−96,000원");
    expect(formatMoney(1_250_000, "KR", { compact: true })).toBe("125만원");
    expect(formatMoney(10_350_000, "KR")).toBe("10,350,000원");
  });

  it("부호", () => {
    expect(formatMoney(-12_300, "KR", { signed: true })).toBe("−12,300원");
    expect(formatMoney(45.2, "US", { signed: true })).toBe("+$45.20");
    expect(formatMoney(10_350.256, "US")).toBe("$10,350.26");
    expect(formatMoney(10_350.256, "US", { compact: true })).toBe("$10,350");
  });

  it("비율", () => {
    expect(formatPercent(0.0321, { signed: true })).toBe("+3.2%");
    expect(formatPercent(-0.0149, { signed: true })).toBe("−1.5%");
    expect(formatPercent(0.00004, { signed: true })).toBe("0.0%");
    expect(formatPercent(0.5, { digits: 0 })).toBe("50%");
    expect(formatPercent(null)).toBe("—");
  });

  it("오르면 up(빨강), 내리면 down(파랑)", () => {
    expect(polarity(1)).toBe("up");
    expect(polarity(-1)).toBe("down");
    expect(polarity(0)).toBe("flat");
    expect(polarity(null)).toBe("flat");
  });
});
