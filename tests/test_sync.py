from datetime import date, datetime
from decimal import Decimal

import pytest

from trading.brokers.fake import FakeBroker
from trading.core.clock import KST
from trading.core.models import Market, Order, OrderStatus, OrderType, Position, Side
from trading.engine.sync import record_new_order, sync_open_orders
from trading.storage.memory import MemoryStore

D = Decimal
KR = Market.KR
MON = date(2026, 10, 26)
TUE = date(2026, 10, 27)


def limit_order(side=Side.BUY, qty=10, limit=70_000, strategy="a") -> Order:
    return Order(
        market=KR,
        symbol="005930",
        side=side,
        qty=qty,
        order_type=OrderType.LIMIT,
        limit_price=D(limit),
        strategy=strategy,
        client_order_id=f"{side}-{strategy}",
    )


@pytest.fixture
def broker() -> FakeBroker:
    b = FakeBroker(cash={KR: 10_000_000}, clock=lambda: datetime(2026, 10, 26, 8, 40, tzinfo=KST))
    b.set_price(KR, "005930", 72_000)  # 지정가 70,000보다 높아 바로 체결되지 않는다
    return b


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


def place(broker, store, order):
    placed = broker.place_order(order)
    record_new_order(KR, store, placed)
    return placed


def test_partial_fills_are_applied_incrementally(broker, store):
    placed = place(broker, store, limit_order(qty=10))
    assert placed.status is OrderStatus.SUBMITTED

    broker.fill_order(placed.broker_order_id, qty=4, price=69_000)
    first = sync_open_orders(KR, broker, store, MON)
    [pos] = store.get_positions(KR)
    assert (pos.qty, pos.avg_price, pos.strategy) == (4, D(69_000), "a")
    assert [(f.qty, f.price) for f in first.fills] == [(4, D(69_000))]

    # 같은 상태로 다시 동기화해도 두 번 반영하지 않는다
    assert sync_open_orders(KR, broker, store, MON).fills == []
    assert store.get_positions(KR)[0].qty == 4

    broker.fill_order(placed.broker_order_id, qty=6, price=70_000)
    second = sync_open_orders(KR, broker, store, MON)
    [pos] = store.get_positions(KR)
    assert pos.qty == 10
    assert pos.avg_price == D(69_600)  # (4×69,000 + 6×70,000) / 10
    assert [(f.qty, f.price) for f in second.fills] == [(6, D(70_000))]
    [rec] = store.get_orders(KR, MON)
    assert (rec.status, rec.filled_qty) == (OrderStatus.FILLED, 10)
    assert store.get_open_orders(KR) == []


def test_position_opened_at_is_order_time(broker, store):
    placed = place(broker, store, limit_order())
    broker.fill_order(placed.broker_order_id)
    sync_open_orders(KR, broker, store, TUE)
    assert store.get_positions(KR)[0].opened_at == placed.created_at


def test_unfilled_order_expires_after_its_day(broker, store):
    place(broker, store, limit_order())
    sync_open_orders(KR, broker, store, MON)
    assert store.get_open_orders(KR)  # 같은 날에는 아직 열려 있다

    sync_open_orders(KR, broker, store, TUE)
    [rec] = store.get_orders(KR, MON)
    assert rec.status is OrderStatus.EXPIRED
    assert store.get_positions(KR) == []


def test_immediate_fill_is_applied_when_recorded(broker, store):
    broker.set_price(KR, "005930", 69_500)
    placed = broker.place_order(limit_order())
    assert placed.status is OrderStatus.FILLED

    result = record_new_order(KR, store, placed)
    assert [(f.qty, f.price) for f in result.fills] == [(10, D(69_500))]
    assert store.get_positions(KR)[0].qty == 10
    assert store.get_orders(KR, MON)[0].status is OrderStatus.FILLED


def test_sell_fill_reduces_then_removes_position(broker, store):
    store.upsert_position(
        Position(
            market=KR,
            symbol="005930",
            strategy="a",
            qty=10,
            avg_price=D(60_000),
            opened_at=datetime(2026, 10, 1, tzinfo=KST),
        )
    )
    broker.set_holding(KR, "005930", 10, 60_000)
    placed = place(broker, store, limit_order(side=Side.SELL, qty=10, limit=75_000))

    broker.fill_order(placed.broker_order_id, qty=3)
    sync_open_orders(KR, broker, store, MON)
    assert store.get_positions(KR)[0].qty == 7

    broker.fill_order(placed.broker_order_id)
    sync_open_orders(KR, broker, store, MON)
    assert store.get_positions(KR) == []


def test_order_missing_at_broker_is_warned(broker, store):
    placed = place(broker, store, limit_order())
    other_broker = FakeBroker(clock=lambda: datetime(2026, 10, 26, 8, 40, tzinfo=KST))
    result = sync_open_orders(KR, other_broker, store, MON)
    assert placed.broker_order_id in result.warnings[0]
    assert store.get_open_orders(KR)  # 상태를 함부로 바꾸지 않는다


def test_rejected_order_is_recorded_as_is(broker, store):
    placed = place(broker, store, limit_order(qty=1000))  # 7천만원: 현금 부족
    assert placed.status is OrderStatus.REJECTED
    [rec] = store.get_orders(KR, MON)
    assert rec.status is OrderStatus.REJECTED
    assert store.get_open_orders(KR) == []
