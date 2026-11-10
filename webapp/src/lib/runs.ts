// 실행 기록(알림함) 읽기 도움 함수.
import type { ReportOrder, RunReport, RunRow, RunStatus, Signal } from "./types";

export const STATUS_META: Record<RunStatus, { label: string; tone: string; icon: string }> = {
  OK: { label: "완료", tone: "good", icon: "✓" },
  PAUSED: { label: "일시정지", tone: "warning", icon: "⏸" },
  SKIPPED: { label: "건너뜀", tone: "neutral", icon: "–" },
  ERROR: { label: "오류", tone: "critical", icon: "!" },
};

/** summary 첫 줄(엔진이 만든 제목과 한국 시간). 예: [모의투자] 국내 · 완료 · 2026-11-10 08:40 KST */
export function runTitle(run: Pick<RunRow, "summary">): string {
  return run.summary.split("\n", 1)[0] ?? "";
}

/** summary에서 제목을 뺀 본문 */
export function runBody(run: Pick<RunRow, "summary">): string {
  const i = run.summary.indexOf("\n");
  return i === -1 ? "" : run.summary.slice(i + 1);
}

export interface RunCounts {
  fills: number;
  orders: number;
  planned: number;
  skipped: number;
  warnings: number;
}

export function runCounts(report: RunReport | undefined): RunCounts {
  return {
    fills: report?.fills?.length ?? 0,
    orders: report?.orders?.length ?? 0,
    planned: report?.planned?.length ?? 0,
    skipped: report?.skipped?.length ?? 0,
    warnings: report?.warnings?.length ?? 0,
  };
}

/** 주문의 근거. 주문 기록에 이유가 있으면 그것, 없으면(옛 기록) 같은 종목·전략의 신호 근거 */
export function orderReason(order: ReportOrder, signals: Signal[] | undefined): string {
  if (order.reason) return order.reason;
  const sig = signals?.find((s) => s.symbol === order.symbol && s.strategy === order.strategy);
  return sig?.reason ?? "";
}

/** 알릴 만한 일이 있는지(홈의 '오늘 일어난 일'에서 강조) */
export function needsAttention(run: RunRow): boolean {
  const c = runCounts(run.report);
  return run.status === "ERROR" || c.warnings > 0;
}

/** 시장별 가장 최근 실행(목록은 최근 순서여야 한다) */
export function latestByMarket<T extends Pick<RunRow, "market">>(runs: T[]): Map<string, T> {
  const out = new Map<string, T>();
  for (const r of runs) if (!out.has(r.market)) out.set(r.market, r);
  return out;
}
