// 보유 종목: 전략별 보유분, 평가손익(마지막 실행 가격 기준), 산 이유.
import { Card, Empty, LoadError, Loading, PnL, Pct, StrategyChip } from "../components/ui";
import { useApp, useAsync } from "../hooks";
import { formatPrice, formatQty, marketLabel, symbolName } from "../lib/format";
import { buyReasons, positionViews } from "../lib/portfolio";
import { MARKETS } from "../lib/types";
import { basisText } from "./Home";

export function Positions() {
  const { source, mode, tick } = useApp();
  const data = useAsync(async () => {
    const [positions, snapshots, orders] = await Promise.all([
      source.listPositions(mode),
      source.latestSnapshots(mode),
      source.filledOrders(mode),
    ]);
    return { positions, snapshots, orders };
  }, [mode, tick]);

  if (data.error && !data.data) return <LoadError error={data.error} />;
  if (!data.data) return <Loading />;
  const { positions, snapshots, orders } = data.data;
  const prices = Object.fromEntries(MARKETS.map((m) => [m, snapshots[m]?.prices ?? {}]));
  const views = positionViews(positions, prices, buyReasons(orders)).sort(
    (a, b) => Date.parse(b.openedAt) - Date.parse(a.openedAt),
  );

  return (
    <div class={data.loading ? "stale" : ""}>
      {data.error && <LoadError error={data.error} />}
      {MARKETS.map((m) => {
        const list = views.filter((v) => v.market === m);
        const snap = snapshots[m];
        return (
          <Card key={m}>
            <h2>{marketLabel(m)}</h2>
            <p class="basis">{snap ? `가격: ${basisText(snap)}` : "가격 기록이 아직 없습니다"}</p>
            {list.length === 0 ? (
              <Empty>보유 종목이 없습니다.</Empty>
            ) : (
              <ul class="rows">
                {list.map((v) => (
                  <li key={`${v.symbol}|${v.strategy}`} class="row">
                    <div class="row-top">
                      <span class="name">{symbolName(v.symbol)}</span>
                      <span class="muted small">{v.symbol}</span>
                      <StrategyChip strategy={v.strategy} />
                      <span class="end">
                        <Pct ratio={v.pnlRatio} />
                      </span>
                    </div>
                    <div class="row-top small" style={{ color: "var(--ink-2)" }}>
                      <span class="num">
                        {formatQty(v.qty)} · 평단 {formatPrice(v.avgPrice, m)} → {formatPrice(v.price, m)}
                      </span>
                      <span class="end">
                        <PnL value={v.pnl} market={m} />
                      </span>
                    </div>
                    <div class="small muted">보유 {v.heldDays}일째</div>
                    {v.reason && <div class="reason">{v.reason}</div>}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        );
      })}
    </div>
  );
}
