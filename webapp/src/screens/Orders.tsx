// 주문: 날짜별 주문 기록(최근 순서), 상태와 체결, 주문 근거.
import { useEffect, useState } from "preact/hooks";
import { Card, Empty, LoadError, Loading, OrderStatusChip, SideChip, StrategyChip } from "../components/ui";
import { useApp } from "../hooks";
import { formatDateKey, formatPrice, formatQty, marketLabel, symbolName } from "../lib/format";
import type { OrderRow } from "../lib/types";

const PAGE = 50;

export function Orders() {
  const { source, mode, tick } = useApp();
  const [orders, setOrders] = useState<OrderRow[] | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    source.listOrders(mode, { limit: PAGE }).then(
      (rows) => {
        if (!alive) return;
        setOrders(rows);
        setMore(rows.length === PAGE);
        setError(null);
      },
      (err: Error) => alive && setError(err),
    );
    return () => {
      alive = false;
    };
  }, [mode, tick]);

  const loadMore = async () => {
    const last = orders?.[orders.length - 1];
    if (!last) return;
    setBusy(true);
    try {
      const rows = await source.listOrders(mode, { limit: PAGE, before: last.created_at });
      setOrders([...(orders ?? []), ...rows]);
      setMore(rows.length === PAGE);
    } catch (err) {
      setError(err as Error);
    } finally {
      setBusy(false);
    }
  };

  if (error && !orders) return <LoadError error={error} />;
  if (!orders) return <Loading />;
  if (orders.length === 0)
    return (
      <Card>
        <Empty>주문 기록이 없습니다.</Empty>
      </Card>
    );

  const days = new Map<string, OrderRow[]>();
  for (const o of orders) days.set(o.trade_date, [...(days.get(o.trade_date) ?? []), o]);

  return (
    <div>
      {error && <LoadError error={error} />}
      {[...days].map(([day, list]) => (
        <section key={day}>
          <h2 class="section-title">{formatDateKey(day)}</h2>
          <Card>
            <ul class="rows">
              {list.map((o) => (
                <li key={o.client_order_id} class="row">
                  <div class="row-top">
                    <SideChip side={o.side} />
                    <span class="name">{symbolName(o.symbol)}</span>
                    <span class="muted small">{marketLabel(o.market)}</span>
                    <StrategyChip strategy={o.strategy} />
                    <span class="end">
                      <OrderStatusChip status={o.status} />
                    </span>
                  </div>
                  <div class="small num" style={{ color: "var(--ink-2)", marginTop: "2px" }}>
                    {formatQty(o.qty)} · {o.limit_price === null ? "시장가" : `지정가 ${formatPrice(o.limit_price, o.market)}`}
                    {o.filled_qty > 0 && ` · 체결 ${formatQty(o.filled_qty)} @ ${formatPrice(o.avg_fill_price, o.market)}`}
                  </div>
                  {o.reason && <div class="reason">{o.reason}</div>}
                  {o.message && <div class={`small ${o.status === "REJECTED" ? "error-text" : "muted"}`}>증권사: {o.message}</div>}
                </li>
              ))}
            </ul>
          </Card>
        </section>
      ))}
      {more && (
        <button class="btn block" onClick={loadMore} disabled={busy}>
          {busy ? "불러오는 중…" : "더 보기"}
        </button>
      )}
    </div>
  );
}
