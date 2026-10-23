from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest
from pydantic import ValidationError

from trading.core.models import Market, Order, OrderType, Side, Signal, validate_bars


def order(**overrides) -> Order:
    fields = {
        "market": Market.KR,
        "symbol": "005930",
        "side": Side.BUY,
        "qty": 10,
        "order_type": OrderType.MARKET,
        "strategy": "trend_ma",
        "client_order_id": "x",
    }
    return Order(**(fields | overrides))


def test_market_currency():
    assert Market.KR.currency == "KRW"
    assert Market.US.currency == "USD"


def test_limit_order_requires_price():
    with pytest.raises(ValidationError, match="limit_price"):
        order(order_type=OrderType.LIMIT)
    assert order(order_type=OrderType.LIMIT, limit_price="71000").limit_price == Decimal(71000)


def test_market_order_rejects_price():
    with pytest.raises(ValidationError):
        order(limit_price="71000")


@pytest.mark.parametrize("qty", [0, -1])
def test_qty_must_be_positive(qty):
    with pytest.raises(ValidationError):
        order(qty=qty)


def test_order_is_immutable():
    o = order()
    with pytest.raises(ValidationError):
        o.qty = 5


def signal(**overrides) -> Signal:
    fields = {
        "market": Market.US,
        "symbol": "AAPL",
        "side": Side.BUY,
        "strategy": "rsi_rebound",
        "price": "180.25",
        "reason": "RSI 28",
    }
    return Signal(**(fields | overrides))


def test_signal_time_stored_as_utc():
    kst = timezone(timedelta(hours=9))
    s = signal(created_at=datetime(2026, 10, 23, 9, 0, tzinfo=kst))
    assert s.created_at == datetime(2026, 10, 23, 0, 0, tzinfo=UTC)
    assert s.created_at.tzinfo == UTC


def test_signal_rejects_naive_time():
    with pytest.raises(ValidationError):
        signal(created_at=datetime(2026, 10, 23, 9, 0))


def test_signal_default_time_is_utc():
    assert signal().created_at.tzinfo == UTC


def _bars(index) -> pd.DataFrame:
    n = len(index)
    return pd.DataFrame(
        {"open": [1] * n, "high": [1] * n, "low": [1] * n, "close": [1] * n, "volume": [1] * n},
        index=index,
    )


def test_validate_bars_ok():
    validate_bars(_bars(pd.bdate_range("2026-01-01", periods=3)))


def test_validate_bars_missing_column():
    df = _bars(pd.bdate_range("2026-01-01", periods=3)).drop(columns="volume")
    with pytest.raises(ValueError, match="volume"):
        validate_bars(df)


def test_validate_bars_unsorted_index():
    index = pd.DatetimeIndex(["2026-01-03", "2026-01-02"])
    with pytest.raises(ValueError, match="오름차순"):
        validate_bars(_bars(index))
