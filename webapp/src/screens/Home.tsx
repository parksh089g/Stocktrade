// 홈: 바뀐 것(오늘 일어난 일)을 먼저, 그다음 시장별 총평가와 전략 비중.
import { AllocationBar } from "../components/AllocationBar";
import { Banner, Card, Empty, LoadError, Loading, PnL, RunStatusChip } from "../components/ui";
import { useApp, useAsync } from "../hooks";
import { formatKst, formatMoney, kstMidnightIso, marketLabel } from "../lib/format";
import { allocation, positionViews } from "../lib/portfolio";
import { latestByMarket, runCounts, runTitle } from "../lib/runs";
import { MARKETS, type RunRow, type Snapshot } from "../lib/types";

const STALE_DAYS = 4;

function countsText(run: RunRow): string {
  const c = runCounts(run.report);
  const parts = [
    c.fills && `체결 ${c.fills}`,
    c.orders && `주문 ${c.orders}`,
    c.planned && `주문 예정 ${c.planned}`,
    c.skipped && `주문 안 함 ${c.skipped}`,
    c.warnings && `확인 필요 ${c.warnings}`,
  ].filter(Boolean);
  if (run.status === "ERROR") return run.error ?? "오류";
  if (run.status === "SKIPPED") return run.message || "건너뜀";
  return parts.length ? parts.join(" · ") : run.report?.stop_loss_only ? "손절할 종목 없음" : "신호 없음";
}

export function basisText(s: Snapshot): string {
  return `${formatKst(s.startedAt)} ${s.stopLossOnly ? "손절 확인 기준(그때 가격)" : "실행 기준(전날 종가)"}`;
}

export function Home() {
  const { source, mode, tick, switchState } = useApp();
  const data = useAsync(async () => {
    const [today, recent, snapshots, positions] = await Promise.all([
      source.runsSince(mode, kstMidnightIso()),
      source.listRuns(mode, { limit: 20 }),
      source.latestSnapshots(mode),
      source.listPositions(mode),
    ]);
    return { today, recent, snapshots, positions };
  }, [mode, tick]);

  if (data.error && !data.data) return <LoadError error={data.error} />;
  if (!data.data) return <Loading />;
  const { today, recent, snapshots, positions } = data.data;
  const latest = latestByMarket(recent);
  const prices = Object.fromEntries(MARKETS.map((m) => [m, snapshots[m]?.prices ?? {}]));
  const views = positionViews(positions, prices, new Map());
  const newest = recent[0];
  const staleDays = newest ? (Date.now() - Date.parse(newest.started_at)) / 86_400_000 : null;

  return (
    <div class={data.loading ? "stale" : ""}>
      {data.error && <LoadError error={data.error} />}
      {switchState.paused && (
        <Banner tone="warning" icon="⏸">
          <strong>일시정지 중</strong>
          새 주문을 내지 않습니다. 손절 매도는 계속 나갑니다.
          {switchState.reason && <div class="small">이유: {switchState.reason}</div>}
          <div class="small muted">{formatKst(switchState.updated_at)}부터</div>
        </Banner>
      )}
      {MARKETS.map((m) => {
        const run = latest.get(m);
        return run?.status === "ERROR" ? (
          <Banner key={m} tone="critical">
            <strong>{marketLabel(m)} 마지막 실행이 오류로 끝났습니다</strong>
            <a href={`/runs/${run.id}`} class="small">
              {formatKst(run.started_at)} 기록 보기
            </a>
          </Banner>
        ) : null;
      })}
      {staleDays !== null && staleDays > STALE_DAYS && (
        <Banner tone="warning">
          <strong>{Math.floor(staleDays)}일째 실행 기록이 없습니다</strong>
          <span class="small">휴장이 길면 정상입니다. 아니면 GitHub Actions와 예약 설정을 확인하세요.</span>
        </Banner>
      )}

      <Card>
        <h2>오늘 일어난 일</h2>
        {today.length === 0 ? (
          <Empty>오늘은 아직 실행 기록이 없습니다.</Empty>
        ) : (
          <ul class="rows">
            {today.map((run) => (
              <li key={run.id}>
                <a class="row-link" href={`/runs/${run.id}`}>
                  <div class="row-top">
                    <span class="name">
                      {marketLabel(run.market)}
                      {runTitle(run).includes("손절 확인") ? " · 손절 확인" : ""}
                    </span>
                    <span class="muted small num">{formatKst(run.started_at).split(" ")[1]}</span>
                    <span class="end">
                      <RunStatusChip status={run.status} />
                    </span>
                  </div>
                  <div class="small" style={{ color: "var(--ink-2)" }}>
                    {countsText(run)}
                  </div>
                </a>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {MARKETS.map((m) => {
        const snap = snapshots[m];
        const alloc = snap ? allocation(snap.balance, views) : null;
        return (
          <Card key={m}>
            <div class="card-head">
              <h2>{marketLabel(m)}</h2>
              <a class="small muted" href="/positions">
                보유 종목 →
              </a>
            </div>
            {!snap || !alloc ? (
              <Empty>잔고 기록이 아직 없습니다. 다음 실행부터 보입니다.</Empty>
            ) : (
              <>
                <p class="basis">{basisText(snap)}</p>
                <div class="muted small">총평가</div>
                <div class="stat-value">{formatMoney(alloc.total, m, { compact: true })}</div>
                <div class="stat-delta">
                  <span class="muted small" style={{ fontWeight: 400 }}>
                    평가손익{" "}
                  </span>
                  <PnL value={alloc.cost > 0 ? alloc.pnl : null} ratio={alloc.cost > 0 ? alloc.pnl / alloc.cost : null} market={m} compact />
                </div>
                <div style={{ marginTop: "14px" }}>
                  <AllocationBar allocation={alloc} market={m} />
                </div>
              </>
            )}
          </Card>
        );
      })}
    </div>
  );
}
