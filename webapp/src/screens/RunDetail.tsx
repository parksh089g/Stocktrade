// 실행 상세(푸시 알림을 누르면 열리는 화면): 체결, 주문과 근거, 주문 안 함과 이유, 확인 필요, 오류.
import type { ComponentChildren } from "preact";
import { Banner, Card, Empty, LoadError, Loading, OrderStatusChip, RunStatusChip, SideChip, StrategyChip } from "../components/ui";
import { useApp, useAsync } from "../hooks";
import { formatKst, formatMoney, formatPrice, formatQty, marketLabel, symbolName } from "../lib/format";
import { orderReason, runTitle } from "../lib/runs";
import type { Market, ReportOrder, RunReport } from "../lib/types";

function Section({ title, children }: { title: string; children: ComponentChildren }) {
  return (
    <section>
      <h2 class="section-title">{title}</h2>
      <Card>
        <ul class="rows">{children}</ul>
      </Card>
    </section>
  );
}

function OrderLine({ order, market }: { order: ReportOrder; market: Market }) {
  return (
    <div class="row-top">
      <SideChip side={order.side} />
      <span class="name">{symbolName(order.symbol)}</span>
      <StrategyChip strategy={order.strategy} />
      <span class="small num" style={{ color: "var(--ink-2)" }}>
        {formatQty(order.qty)} · {order.limit_price === null ? "시장가" : `지정가 ${formatPrice(order.limit_price, market)}`}
      </span>
    </div>
  );
}

function Details({ report }: { report: RunReport }) {
  const m = report.market;
  const fills = report.fills ?? [];
  const orders = report.orders ?? [];
  const planned = report.planned ?? [];
  const skipped = report.skipped ?? [];
  const warnings = report.warnings ?? [];
  const nothing = !fills.length && !orders.length && !planned.length && !skipped.length && !warnings.length && !report.error;
  return (
    <>
      {report.error && (
        <Banner tone="critical">
          <strong>오류</strong>
          {report.error}
        </Banner>
      )}
      {report.message && <Banner tone="info">{report.message}</Banner>}
      {warnings.length > 0 && (
        <Section title={`확인 필요 ${warnings.length}건`}>
          {warnings.map((w, i) => (
            <li key={i} class="row">
              {w}
            </li>
          ))}
        </Section>
      )}
      {fills.length > 0 && (
        <Section title={`새 체결 ${fills.length}건`}>
          {fills.map((f, i) => (
            <li key={i} class="row">
              <div class="row-top">
                <SideChip side={f.order.side} />
                <span class="name">{symbolName(f.order.symbol)}</span>
                <StrategyChip strategy={f.order.strategy} />
                <span class="small num" style={{ color: "var(--ink-2)" }}>
                  {formatQty(f.qty)} @ {formatPrice(f.price, m)}
                </span>
              </div>
            </li>
          ))}
        </Section>
      )}
      {orders.length > 0 && (
        <Section title={`주문 ${orders.length}건`}>
          {orders.map((r) => (
            <li key={r.order.client_order_id} class="row">
              <OrderLine order={r.order} market={m} />
              <div class="row-top" style={{ marginTop: "4px" }}>
                <OrderStatusChip status={r.status} />
                {r.message && <span class={`small ${r.status === "REJECTED" ? "error-text" : "muted"}`}>{r.message}</span>}
              </div>
              {orderReason(r.order, report.signals) && <div class="reason">{orderReason(r.order, report.signals)}</div>}
            </li>
          ))}
        </Section>
      )}
      {planned.length > 0 && (
        <Section title={`주문 예정 ${planned.length}건 (시험 실행이라 내지 않음)`}>
          {planned.map((o) => (
            <li key={o.client_order_id} class="row">
              <OrderLine order={o} market={m} />
              {orderReason(o, report.signals) && <div class="reason">{orderReason(o, report.signals)}</div>}
            </li>
          ))}
        </Section>
      )}
      {skipped.length > 0 && (
        <Section title={`주문 안 함 ${skipped.length}건`}>
          {skipped.map((s, i) => (
            <li key={i} class="row">
              <div class="row-top">
                <SideChip side={s.signal.side} />
                <span class="name">{symbolName(s.signal.symbol)}</span>
                <StrategyChip strategy={s.signal.strategy} />
              </div>
              <div style={{ marginTop: "4px", fontWeight: 600 }}>{s.reason}</div>
              {s.signal.reason && <div class="reason">{s.signal.reason}</div>}
            </li>
          ))}
        </Section>
      )}
      {nothing && (
        <Card>
          <Empty>{report.stop_loss_only ? "손절할 종목이 없었습니다." : "이날은 신호가 없었습니다."}</Empty>
        </Card>
      )}
      {report.balance && (
        <Card>
          <h2>계좌(주문 전)</h2>
          <dl class="kv">
            <div>
              <dt>총평가</dt>
              <dd>{formatMoney(report.balance.total_equity, m, { compact: true })}</dd>
            </div>
            <div>
              <dt>주문 가능 현금</dt>
              <dd>{formatMoney(report.balance.cash, m, { compact: true })}</dd>
            </div>
            <div>
              <dt>{report.benchmark?.name ?? "비교 지수"}</dt>
              <dd>{report.benchmark ? formatPrice(report.benchmark.close, m) : "—"}</dd>
            </div>
          </dl>
        </Card>
      )}
    </>
  );
}

export function RunDetail({ id }: { id: number }) {
  const { source, tick } = useApp();
  const data = useAsync(() => source.getRun(id), [id, tick]);
  if (data.error && !data.data) return <LoadError error={data.error} />;
  if (data.data === undefined) return <Loading />;
  const run = data.data;
  if (!run)
    return (
      <Card>
        <Empty>기록을 찾지 못했습니다.</Empty>
      </Card>
    );
  return (
    <div class={data.loading ? "stale" : ""}>
      <Card>
        <div class="row-top">
          <span class="name">{marketLabel(run.market)}</span>
          <RunStatusChip status={run.status} />
          <span class="end muted small num">{formatKst(run.started_at)}</span>
        </div>
        <div class="small muted" style={{ marginTop: "4px" }}>
          {runTitle(run)}
        </div>
      </Card>
      {run.report ? (
        <Details report={run.report} />
      ) : (
        <Card>
          <pre class="summary">{run.summary}</pre>
        </Card>
      )}
      <details class="card">
        <summary class="small muted">알림 원문</summary>
        <pre class="summary" style={{ marginTop: "8px" }}>
          {run.summary}
        </pre>
      </details>
    </div>
  );
}
