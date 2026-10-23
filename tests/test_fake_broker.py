from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd
import pytest

from trading.brokers.base import BrokerError, UnsupportedOrderError
from trading.brokers.fake import FakeBroker, make_random_bars
from trading.core.clock import trading_date
from trading.core.models import Market, Order, OrderStatus, OrderType, Side, validate_bars

D = Decimal


def make_order(market=Market.KR, symbol="005930", side=Side.BUY, qty=10, limit=None) -> Order:
    return Order(
        market=market,
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=OrderType.LIMIT if limit is not None else OrderType.MARKET,
        limit_price=limit,
        strategy="trend_ma",
        client_order_id="test",
    )


# 한국 10/23 10:00, 뉴욕 10/22 21:00
NOW = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)


@pytest.fixture
def broker() -> FakeBroker:
    b = FakeBroker(cash={Market.KR: 1_000_000, Market.US: 10_000}, clock=lambda: NOW)
    b.set_price(Market.KR, "005930", 70_000)
    b.set_price(Market.US, "AAPL", "180.50")
    return b


def test_kr_market_buy_fills_at_current_price(broker: FakeBroker):
    result = broker.place_order(make_order(qty=10))

    assert result.status is OrderStatus.FILLED
    assert result.filled_qty == 10
    assert result.avg_fill_price == D(70_000)
    assert result.broker_order_id
    assert broker.get_balance(Market.KR).cash == D(300_000)
    [holding] = broker.get_holdings(Market.KR)
    assert (holding.symbol, holding.qty, holding.avg_price) == ("005930", 10, D(70_000))


def test_us_market_order_is_refused_like_kis(broker: FakeBroker):
    assert broker.supported_order_types(Market.US) == {OrderType.LIMIT}
    with pytest.raises(UnsupportedOrderError):
        broker.place_order(make_order(market=Market.US, symbol="AAPL", qty=1))
    assert broker.get_orders(Market.US, trading_date(Market.US, NOW)) == []


def test_marketable_limit_buy_fills_at_current_price(broker: FakeBroker):
    result = broker.place_order(make_order(Market.US, "AAPL", qty=10, limit=D("181")))
    assert result.status is OrderStatus.FILLED
    assert result.avg_fill_price == D("180.50")
    assert broker.get_balance(Market.US).cash == D("8195.00")


def test_limit_buy_below_price_waits_and_reserves_cash(broker: FakeBroker):
    result = broker.place_order(make_order(Market.US, "AAPL", qty=50, limit=D("175")))

    assert result.status is OrderStatus.SUBMITTED
    assert broker.get_holdings(Market.US) == []
    # 접수 중인 매수 금액만큼 주문 가능 현금이 줄어든다
    assert broker.get_balance(Market.US).cash == D(10_000) - 50 * D(175)
    assert broker.get_balance(Market.US).total_equity == D(10_000)

    broker.set_price(Market.US, "AAPL", "174.00")
    broker.process_open_orders()

    [order] = broker.get_orders(Market.US, date(2026, 10, 22))
    assert order.status is OrderStatus.FILLED
    assert order.avg_fill_price == D("174.00")
    assert broker.get_holdings(Market.US)[0].qty == 50


def test_reserved_cash_blocks_second_buy(broker: FakeBroker):
    broker.place_order(make_order(Market.US, "AAPL", qty=50, limit=D("175")))  # 8750 예약
    result = broker.place_order(make_order(Market.US, "AAPL", qty=10, limit=D("176")))
    assert result.status is OrderStatus.REJECTED


def test_insufficient_cash_is_rejected_without_state_change(broker: FakeBroker):
    result = broker.place_order(make_order(qty=100))  # 7,000,000원 필요

    assert result.status is OrderStatus.REJECTED
    assert "금액" in result.message
    assert broker.get_balance(Market.KR).cash == D(1_000_000)
    assert broker.get_holdings(Market.KR) == []


def test_sell_more_than_held_is_rejected(broker: FakeBroker):
    broker.set_holding(Market.KR, "005930", 5, 60_000)
    result = broker.place_order(make_order(side=Side.SELL, qty=6))
    assert result.status is OrderStatus.REJECTED
    assert broker.get_holdings(Market.KR)[0].qty == 5


def test_partial_then_full_sell(broker: FakeBroker):
    broker.set_holding(Market.KR, "005930", 10, 60_000)

    broker.place_order(make_order(side=Side.SELL, qty=4))
    [holding] = broker.get_holdings(Market.KR)
    assert (holding.qty, holding.avg_price) == (6, D(60_000))
    assert broker.get_balance(Market.KR).cash == D(1_000_000 + 4 * 70_000)

    broker.place_order(make_order(side=Side.SELL, qty=6))
    assert broker.get_holdings(Market.KR) == []


def test_buy_updates_average_price(broker: FakeBroker):
    broker.set_holding(Market.KR, "005930", 10, 60_000)
    broker.place_order(make_order(qty=10))  # 70,000원에 10주
    assert broker.get_holdings(Market.KR)[0].avg_price == D(65_000)


def test_balance_marks_holdings_to_market(broker: FakeBroker):
    broker.set_holding(Market.KR, "005930", 10, 60_000)
    balance = broker.get_balance(Market.KR)
    assert balance.total_equity == D(1_000_000 + 10 * 70_000)
    assert balance.currency == "KRW"


def test_fee_is_charged(broker: FakeBroker):
    b = FakeBroker(cash={Market.KR: 1_000_000}, fee_rate=D("0.001"))
    b.set_price(Market.KR, "005930", 70_000)
    b.place_order(make_order(qty=10))
    assert b.get_balance(Market.KR).cash == D(1_000_000) - D(700_000) - D(700)


def test_orders_are_grouped_by_market_local_date():
    # 한국 10/24 00:30 = 뉴욕 10/23 11:30
    at = datetime(2026, 10, 23, 15, 30, tzinfo=UTC)
    b = FakeBroker(cash={Market.KR: 1_000_000}, clock=lambda: at)
    b.set_price(Market.KR, "005930", 70_000)
    b.place_order(make_order(qty=1))

    assert len(b.get_orders(Market.KR, date(2026, 10, 24))) == 1
    assert b.get_orders(Market.KR, date(2026, 10, 23)) == []


def test_fail_on_raises_broker_error(broker: FakeBroker):
    broker.fail_on.add("place_order")
    with pytest.raises(BrokerError):
        broker.place_order(make_order(qty=1))
    assert broker.get_holdings(Market.KR) == []


def test_unknown_symbol_errors(broker: FakeBroker):
    with pytest.raises(BrokerError):
        broker.get_price(Market.KR, "000000")
    with pytest.raises(BrokerError):
        broker.get_daily_bars(Market.KR, "000000", 10)
    result = broker.place_order(make_order(symbol="000000", qty=1))
    assert result.status is OrderStatus.REJECTED


def test_bars_are_copied_and_set_price(broker: FakeBroker):
    bars = make_random_bars(100, seed=1)
    broker.set_bars(Market.KR, "000660", bars)

    got = broker.get_daily_bars(Market.KR, "000660", 20)
    assert len(got) == 20
    assert got.index[-1] == bars.index[-1]
    assert broker.get_price(Market.KR, "000660") == D(str(bars["close"].iloc[-1]))

    got.loc[got.index[-1], "close"] = -1
    assert broker.get_daily_bars(Market.KR, "000660", 1)["close"].iloc[-1] > 0


def test_random_bars_are_valid_and_deterministic():
    a = make_random_bars(250, seed=7)
    b = make_random_bars(250, seed=7)
    validate_bars(a)
    pd.testing.assert_frame_equal(a, b)
    assert (a["high"] >= a[["open", "close"]].max(axis=1)).all()
    assert (a["low"] <= a[["open", "close"]].min(axis=1)).all()
    assert (a["low"] > 0).all()
