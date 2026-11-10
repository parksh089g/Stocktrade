// 전략별 비중: 한 묶음(총평가)을 나눈 막대 + 범례를 겸한 표(금액, 비중, 목표).
import { formatMoney, formatPercent } from "../lib/format";
import type { Allocation } from "../lib/portfolio";
import type { Market } from "../lib/types";

const PART: Record<string, { name: string; fill: string; ink: string }> = {
  trend_ma: { name: "추세 추종", fill: "var(--s-trend)", ink: "var(--s-trend-ink)" },
  rsi_rebound: { name: "단기 반등", fill: "var(--s-rsi)", ink: "var(--s-rsi-ink)" },
  cash: { name: "현금(주문 가능)", fill: "var(--s-cash)", ink: "var(--s-cash-ink)" },
  other: { name: "기타(주문 대기 금액 등)", fill: "var(--s-other)", ink: "var(--s-other-ink)" },
};

// 막대 칸 안에 비율 글자를 넣을 최소 비중(폰 너비에서 '12%'가 여유 있게 들어가는 정도)
const LABEL_MIN_SHARE = 0.12;

function part(key: string) {
  return PART[key] ?? { name: key, fill: "var(--axis)", ink: "var(--ink)" };
}

export function AllocationBar({ allocation, market }: { allocation: Allocation; market: Market }) {
  const shown = allocation.parts.filter((p) => p.value > 0);
  const described = shown.map((p) => `${part(p.key).name} ${formatPercent(p.share, { digits: 0 })}`).join(", ");
  const rows = allocation.parts.filter((p) => p.key !== "other" || p.value > 0);
  return (
    <div>
      <div class="seg" role="img" aria-label={`비중: ${described}`}>
        {shown.map((p) => (
          <span key={p.key} style={{ flex: `${p.share} 1 0`, background: part(p.key).fill, color: part(p.key).ink }}>
            {p.share >= LABEL_MIN_SHARE ? formatPercent(p.share, { digits: 0 }) : ""}
          </span>
        ))}
      </div>
      <table class="alloc">
        <tbody>
          {rows.map((p) => (
            <tr key={p.key}>
              <td>
                <i class="swatch" style={{ background: part(p.key).fill }} aria-hidden="true" />
                {part(p.key).name}
              </td>
              <td>{formatMoney(p.value, market, { compact: true })}</td>
              <td>{formatPercent(p.share, { digits: 0 })}</td>
              <td class="muted">{p.target === null ? "" : `목표 ${formatPercent(p.target, { digits: 0 })}`}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {allocation.approximate && <p class="basis">가격을 모르는 종목은 평단으로 셈했습니다.</p>}
    </div>
  );
}
