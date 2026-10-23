"""체결 동기화: 지난 실행에서 낸 주문의 체결 결과를 증권사에서 확인해 전략별 보유분에 반영한다.

장 시작 전에 주문하므로 체결은 실행이 끝난 뒤에 일어난다. 그래서 매 실행 처음에 이 단계를
거친다. 같은 체결을 두 번 반영하지 않도록, 주문 기록의 filled_qty(반영한 수량)와 비교해
늘어난 만큼만 반영한다.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from trading.brokers.base import BrokerAdapter
from trading.core.clock import trading_date
from trading.core.models import Market, OrderResult, OrderStatus, Position, Side
from trading.engine.report import FillUpdate
from trading.storage.base import Store


@dataclass
class SyncResult:
    fills: list[FillUpdate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def sync_open_orders(
    market: Market, broker: BrokerAdapter, store: Store, today: date
) -> SyncResult:
    result = SyncResult()
    by_day: dict[date, list[OrderResult]] = defaultdict(list)
    for rec in store.get_open_orders(market):
        by_day[trading_date(market, rec.created_at)].append(rec)

    for day in sorted(by_day):
        at_broker = {r.broker_order_id: r for r in broker.get_orders(market, day)}
        for rec in by_day[day]:
            current = at_broker.get(rec.broker_order_id)
            if current is None:
                order_ref = f"{rec.order.symbol} 주문({rec.broker_order_id}, {day})"
                result.warnings.append(f"{order_ref}을 증권사에서 찾지 못했습니다")
                continue
            apply_order_update(market, store, rec, current, day_is_over=day < today, result=result)
    return result


def record_new_order(market: Market, store: Store, placed: OrderResult) -> SyncResult:
    """방금 낸 주문을 기록한다. 바로 체결된 부분이 있으면 보유분에도 반영한다."""
    result = SyncResult()
    if placed.status is OrderStatus.REJECTED:
        store.save_order(placed)
        return result
    # 먼저 '아직 반영 안 됨'으로 저장한 뒤 체결분을 반영한다.
    # 중간에 실패해도 다음 동기화가 이어받는다.
    pending = OrderResult.model_validate(
        {
            **placed.model_dump(),
            "status": OrderStatus.SUBMITTED,
            "filled_qty": 0,
            "avg_fill_price": None,
        }
    )
    store.save_order(pending)
    apply_order_update(market, store, pending, placed, day_is_over=False, result=result)
    return result


def apply_order_update(
    market: Market,
    store: Store,
    rec: OrderResult,
    current: OrderResult,
    *,
    day_is_over: bool,
    result: SyncResult,
) -> None:
    """저장된 주문 기록(rec)을 증권사의 최신 상태(current)로 갱신하고, 늘어난 체결을 반영한다."""
    symbol = rec.order.symbol
    delta = current.filled_qty - rec.filled_qty
    if delta < 0:
        result.warnings.append(f"{symbol}: 증권사 체결 수량이 기록보다 적습니다. 확인이 필요합니다")
        return

    if delta > 0:
        price = _incremental_price(rec, current, delta)
        if price is None:
            result.warnings.append(f"{symbol}: 체결 가격을 알 수 없어 보유분에 반영하지 못했습니다")
            return
        # 보유분을 먼저 바꾸고 주문 기록을 나중에 바꾼다. 중간에 실패하면 다음 실행에서
        # 같은 체결이 한 번 더 반영될 수 있는데, 이는 계좌 수량 대조 경고로 드러난다.
        _apply_fill(market, store, rec, delta, price, result)
        result.fills.append(FillUpdate(order=rec.order, qty=delta, price=price))

    status = current.status
    if status.is_open and day_is_over:
        status = OrderStatus.EXPIRED
    store.save_order(
        OrderResult.model_validate(
            {
                **rec.model_dump(),
                "status": status,
                "filled_qty": current.filled_qty,
                "avg_fill_price": current.avg_fill_price,
                "message": current.message or rec.message,
            }
        )
    )


def _incremental_price(rec: OrderResult, current: OrderResult, delta: int) -> Decimal | None:
    """이번에 늘어난 체결분의 평균 가격."""
    if current.avg_fill_price is None:
        return None
    if rec.filled_qty == 0 or rec.avg_fill_price is None:
        return current.avg_fill_price
    total = current.filled_qty * current.avg_fill_price
    before = rec.filled_qty * rec.avg_fill_price
    return (total - before) / delta


def _apply_fill(
    market: Market,
    store: Store,
    rec: OrderResult,
    qty: int,
    price: Decimal,
    result: SyncResult,
) -> None:
    order = rec.order
    pos = next(
        (
            p
            for p in store.get_positions(market)
            if p.symbol == order.symbol and p.strategy == order.strategy
        ),
        None,
    )

    if order.side is Side.BUY:
        if pos is None:
            # 장 시작 전 주문은 그날 체결되므로 주문 시각을 보유 시작 시각으로 쓴다
            new = Position(
                market=market,
                symbol=order.symbol,
                strategy=order.strategy,
                qty=qty,
                avg_price=price,
                opened_at=rec.created_at,
            )
        else:
            total = pos.qty + qty
            new = Position.model_validate(
                {
                    **pos.model_dump(),
                    "qty": total,
                    "avg_price": (pos.qty * pos.avg_price + qty * price) / total,
                }
            )
        store.upsert_position(new)
        return

    if pos is None:
        result.warnings.append(
            f"{order.symbol}: 매도가 체결됐지만 {order.strategy} 보유 기록이 없습니다"
        )
        return
    left = pos.qty - qty
    if left > 0:
        store.upsert_position(Position.model_validate({**pos.model_dump(), "qty": left}))
        return
    if left < 0:
        result.warnings.append(
            f"{order.symbol}: 매도 체결 {qty}주가 {order.strategy} 기록 {pos.qty}주보다 많습니다"
        )
    store.delete_position(market, order.symbol, order.strategy)
