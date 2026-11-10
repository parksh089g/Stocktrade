import type { ComponentChildren } from "preact";
import { formatMoney, formatPercent, polarity, sideLabel, strategyLabel } from "../lib/format";
import { STATUS_META } from "../lib/runs";
import type { Market, OrderStatus, RunStatus, Side } from "../lib/types";

export function Card(props: { children: ComponentChildren; class?: string }) {
  return <section class={`card ${props.class ?? ""}`}>{props.children}</section>;
}

/** 매수(빨강)/매도(파랑). 색과 글자를 함께 쓴다 */
export function SideChip({ side }: { side: Side }) {
  return <span class={`chip ${side === "BUY" ? "buy" : "sell"}`}>{sideLabel(side)}</span>;
}

const STRATEGY_COLOR: Record<string, string> = { trend_ma: "var(--s-trend)", rsi_rebound: "var(--s-rsi)" };

export function strategyColor(strategy: string): string {
  return STRATEGY_COLOR[strategy] ?? "var(--axis)";
}

export function StrategyChip({ strategy }: { strategy: string }) {
  return (
    <span class="chip">
      <i class="swatch" style={{ background: strategyColor(strategy) }} aria-hidden="true" />
      {strategyLabel(strategy)}
    </span>
  );
}

/** 실행 상태. 상태 색은 아이콘·글자와 함께만 쓴다 */
export function RunStatusChip({ status }: { status: RunStatus }) {
  const meta = STATUS_META[status];
  return (
    <span class={`chip ${meta.tone}`}>
      <span aria-hidden="true">{meta.icon}</span>
      {meta.label}
    </span>
  );
}

const ORDER_STATUS: Record<OrderStatus, { label: string; tone: string }> = {
  SUBMITTED: { label: "접수", tone: "" },
  PARTIALLY_FILLED: { label: "부분체결", tone: "" },
  FILLED: { label: "체결", tone: "good" },
  REJECTED: { label: "거부", tone: "critical" },
  CANCELLED: { label: "취소", tone: "" },
  EXPIRED: { label: "만료", tone: "" },
};

export function OrderStatusChip({ status }: { status: OrderStatus }) {
  const meta = ORDER_STATUS[status] ?? { label: status, tone: "" };
  return <span class={`chip ${meta.tone}`}>{meta.label}</span>;
}

/** 손익 금액과 비율. 오르면 빨강 +, 내리면 파랑 − */
export function PnL(props: { value: number | null; ratio?: number | null; market: Market; compact?: boolean }) {
  if (props.value === null) return <span class="muted">—</span>;
  const tone = polarity(props.value);
  return (
    <span class={`num ${tone === "flat" ? "" : tone}`}>
      {formatMoney(props.value, props.market, { signed: true, compact: props.compact })}
      {props.ratio !== undefined && props.ratio !== null && ` (${formatPercent(props.ratio, { signed: true })})`}
    </span>
  );
}

export function Pct({ ratio }: { ratio: number | null }) {
  const tone = polarity(ratio);
  return <span class={`num ${tone === "flat" ? "" : tone}`}>{formatPercent(ratio, { signed: true })}</span>;
}

export function Banner(props: { tone: "warning" | "critical" | "info"; icon?: string; children: ComponentChildren }) {
  return (
    <div class={`banner ${props.tone}`} role={props.tone === "info" ? undefined : "alert"}>
      <span class="icon" aria-hidden="true">
        {props.icon ?? (props.tone === "info" ? "i" : "!")}
      </span>
      <div>{props.children}</div>
    </div>
  );
}

export function Empty({ children }: { children: ComponentChildren }) {
  return <p class="empty">{children}</p>;
}

export function LoadError({ error }: { error: Error }) {
  return (
    <Banner tone="critical">
      <strong>불러오지 못했습니다</strong>
      <span class="small">{error.message}</span>
    </Banner>
  );
}

export function Loading() {
  return <Empty>불러오는 중…</Empty>;
}

// ---- 아이콘(선 아이콘, 글자 색을 따른다) ----

function Svg({ children }: { children: ComponentChildren }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      {children}
    </svg>
  );
}

export const Icons = {
  home: () => (
    <Svg>
      <path d="M3 10.5 12 3l9 7.5" />
      <path d="M5 9.5V21h14V9.5" />
    </Svg>
  ),
  holdings: () => (
    <Svg>
      <rect x="3" y="7" width="18" height="13" rx="2" />
      <path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </Svg>
  ),
  orders: () => (
    <Svg>
      <path d="M8 6h13M8 12h13M8 18h13" />
      <path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01" />
    </Svg>
  ),
  inbox: () => (
    <Svg>
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.9 1.9 0 0 0 3.4 0" />
    </Svg>
  ),
  chart: () => (
    <Svg>
      <path d="M3 3v18h18" />
      <path d="m7 15 4-4 3 3 5-6" />
    </Svg>
  ),
  settings: () => (
    <Svg>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </Svg>
  ),
  refresh: () => (
    <Svg>
      <path d="M21 12a9 9 0 1 1-2.6-6.4" />
      <path d="M21 3v6h-6" />
    </Svg>
  ),
  back: () => (
    <Svg>
      <path d="m15 18-6-6 6-6" />
    </Svg>
  ),
};
