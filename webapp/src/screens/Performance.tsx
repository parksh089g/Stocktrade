// 성적: 시장별 지수 대비 수익 곡선과 전략별 성적표. 기록은 2026-11-10부터 쌓인다.
import { EquityChart } from "../components/EquityChart";
import { Card, Empty, LoadError, Loading, PnL, Pct, strategyColor } from "../components/ui";
import { useApp, useAsync } from "../hooks";
import { formatDateKey, formatPercent, marketLabel, strategyLabel } from "../lib/format";
import { equityPoints, indexed, strategyStats, type StrategyStats } from "../lib/performance";
import { positionViews, STRATEGY_ORDER } from "../lib/portfolio";
import { MARKETS, type Market } from "../lib/types";

const MIN_POINTS = 2;

function StrategyBlock({ s }: { s: StrategyStats }) {
  return (
    <li class="row">
      <div class="row-top">
        <i class="swatch" style={{ background: strategyColor(s.strategy) }} aria-hidden="true" />
        <span class="name">{strategyLabel(s.strategy, true)}</span>
      </div>
      <dl class="kv">
        <div>
          <dt>끝난 거래</dt>
          <dd>{s.closedTrades}건</dd>
        </div>
        <div>
          <dt>승률</dt>
          <dd>{s.winRate === null ? "—" : formatPercent(s.winRate, { digits: 0 })}</dd>
        </div>
        <div>
          <dt>평균 수익률</dt>
          <dd>
            <Pct ratio={s.avgReturn} />
          </dd>
        </div>
        <div>
          <dt>실현손익</dt>
          <dd>
            <PnL value={s.closedTrades ? s.realized : null} market={s.market} compact />
          </dd>
        </div>
        <div>
          <dt>보유 중</dt>
          <dd>{s.openCount}종목</dd>
        </div>
        <div>
          <dt>평가손익</dt>
          <dd>
            <PnL value={s.unrealized} market={s.market} compact />
          </dd>
        </div>
      </dl>
    </li>
  );
}

export function Performance() {
  const { source, mode, tick } = useApp();
  const data = useAsync(async () => {
    const [history, orders, positions, snapshots] = await Promise.all([
      source.equityHistory(mode),
      source.filledOrders(mode),
      source.listPositions(mode),
      source.latestSnapshots(mode),
    ]);
    return { history, orders, positions, snapshots };
  }, [mode, tick]);

  if (data.error && !data.data) return <LoadError error={data.error} />;
  if (!data.data) return <Loading />;
  const { history, orders, positions, snapshots } = data.data;
  const prices = Object.fromEntries(MARKETS.map((m) => [m, snapshots[m]?.prices ?? {}]));
  const stats = strategyStats(orders, positionViews(positions, prices, new Map()));

  const statsFor = (m: Market) =>
    stats
      .filter((s) => s.market === m)
      .sort((a, b) => STRATEGY_ORDER.indexOf(a.strategy) - STRATEGY_ORDER.indexOf(b.strategy));

  return (
    <div class={data.loading ? "stale" : ""}>
      {data.error && <LoadError error={data.error} />}
      {MARKETS.map((m) => {
        const points = indexed(equityPoints(history, m));
        const benchName = history.find((r) => r.market === m && r.benchmark)?.benchmark?.name ?? (m === "KR" ? "KODEX 200" : "SPY");
        const first = points[0];
        const last = points[points.length - 1];
        return (
          <section key={m}>
            <h2 class="section-title">{marketLabel(m)}</h2>
            <Card>
              <h2>계좌 vs {benchName}</h2>
              {points.length < MIN_POINTS || !first || !last ? (
                <Empty>
                  기록이 쌓이는 중입니다({points.length}일). 매매 실행이 이틀 이상 쌓이면 곡선이 보입니다.
                </Empty>
              ) : (
                <>
                  <p class="basis">
                    {formatDateKey(first.date)}=100 · 계좌 <Pct ratio={last.account / 100 - 1} />
                    {last.bench !== null && (
                      <>
                        {" "}
                        · {benchName} <Pct ratio={last.bench / 100 - 1} />
                      </>
                    )}
                  </p>
                  <EquityChart points={points} benchName={benchName} />
                </>
              )}
            </Card>
            <Card>
              <h2>전략별 성적</h2>
              <p class="basis">체결 가격 기준, 수수료·세금 제외</p>
              {statsFor(m).length === 0 ? (
                <Empty>체결된 거래가 아직 없습니다.</Empty>
              ) : (
                <ul class="rows">
                  {statsFor(m).map((s) => (
                    <StrategyBlock key={s.strategy} s={s} />
                  ))}
                </ul>
              )}
            </Card>
          </section>
        );
      })}
      <p class="basis" style={{ padding: "0 2px" }}>
        수익 곡선은 하루 한 점(그날 첫 매매 실행)입니다. 입금·출금이 있으면 그날 곡선이 튈 수 있습니다.
      </p>
    </div>
  );
}
