"""메모리 안에서만 동작하는 가짜 증권사. 테스트와 로컬 실행에 쓴다.

KIS와 같은 제약을 기본으로 흉내 낸다: 해외(US)는 지정가 주문만 받는다.
체결 규칙:
- 시장가: 현재가에 바로 체결
- 지정가: 현재가로 바로 체결할 수 있으면(매수 지정가 >= 현재가, 매도 지정가 <= 현재가)
  현재가에 체결, 아니면 접수 상태로 남는다. process_open_orders()로 다시 맞춰 본다.
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd

from trading.brokers.base import BrokerAdapter, BrokerError
from trading.core.clock import now_utc, trading_date
from trading.core.models import (
    Balance,
    Holding,
    Market,
    Order,
    OrderResult,
    OrderStatus,
    OrderType,
    Side,
    validate_bars,
)

Key = tuple[Market, str]


class FakeBroker(BrokerAdapter):
    name = "fake"

    def __init__(
        self,
        cash: Mapping[Market, Decimal | int | str] | None = None,
        *,
        market_order_markets: Iterable[Market] = (Market.KR,),
        fee_rate: Decimal = Decimal("0"),
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        cash = cash or {}
        self._cash: dict[Market, Decimal] = {m: Decimal(str(cash.get(m, 0))) for m in Market}
        self._market_order_markets = frozenset(market_order_markets)
        self._fee_rate = fee_rate
        self._clock = clock
        self._bars: dict[Key, pd.DataFrame] = {}
        self._prices: dict[Key, Decimal] = {}
        self._holdings: dict[Key, tuple[int, Decimal]] = {}  # (수량, 평균단가)
        self._orders: list[OrderResult] = []
        self._seq = 0
        # 메서드 이름을 넣으면 그 메서드가 BrokerError를 던진다 (오류 상황 테스트용)
        self.fail_on: set[str] = set()

    # ---- 테스트/로컬 실행용 준비 함수 ----

    def set_bars(self, market: Market, symbol: str, bars: pd.DataFrame) -> None:
        """일봉을 넣고, 마지막 종가를 현재가로 쓴다."""
        validate_bars(bars)
        self._bars[(market, symbol)] = bars.copy()
        self._prices[(market, symbol)] = Decimal(str(bars["close"].iloc[-1]))

    def set_price(self, market: Market, symbol: str, price: Decimal | int | str) -> None:
        self._prices[(market, symbol)] = Decimal(str(price))

    def set_holding(
        self, market: Market, symbol: str, qty: int, avg_price: Decimal | int | str
    ) -> None:
        self._holdings[(market, symbol)] = (qty, Decimal(str(avg_price)))

    def process_open_orders(self) -> None:
        """접수 상태인 지정가 주문을 현재가와 다시 맞춰 본다."""
        for i, result in enumerate(self._orders):
            if result.status is OrderStatus.SUBMITTED:
                price = self._prices.get((result.order.market, result.order.symbol))
                if price is not None and _is_marketable(result.order, price):
                    self._orders[i] = self._fill(result, price)

    # ---- BrokerAdapter ----

    def supported_order_types(self, market: Market) -> frozenset[OrderType]:
        if market in self._market_order_markets:
            return frozenset({OrderType.MARKET, OrderType.LIMIT})
        return frozenset({OrderType.LIMIT})

    def get_daily_bars(self, market: Market, symbol: str, count: int) -> pd.DataFrame:
        self._maybe_fail("get_daily_bars")
        bars = self._bars.get((market, symbol))
        if bars is None:
            raise BrokerError(f"fake: {market} {symbol} 일봉 데이터가 없습니다")
        return bars.tail(count).copy()

    def get_price(self, market: Market, symbol: str) -> Decimal:
        self._maybe_fail("get_price")
        price = self._prices.get((market, symbol))
        if price is None:
            raise BrokerError(f"fake: {market} {symbol} 시세가 없습니다")
        return price

    def get_balance(self, market: Market) -> Balance:
        self._maybe_fail("get_balance")
        holdings_value = sum(
            (
                qty * self._prices.get(key, avg)
                for key, (qty, avg) in self._holdings.items()
                if key[0] == market
            ),
            Decimal(0),
        )
        cash = self._cash[market]
        return Balance(
            market=market,
            cash=max(cash - self._reserved_cash(market), Decimal(0)),
            total_equity=cash + holdings_value,
        )

    def get_holdings(self, market: Market) -> list[Holding]:
        self._maybe_fail("get_holdings")
        return [
            Holding(market=m, symbol=s, qty=qty, avg_price=avg)
            for (m, s), (qty, avg) in self._holdings.items()
            if m == market
        ]

    def get_orders(self, market: Market, day: date) -> list[OrderResult]:
        self._maybe_fail("get_orders")
        return [
            r
            for r in self._orders
            if r.order.market == market and trading_date(market, r.created_at) == day
        ]

    def _submit_order(self, order: Order) -> OrderResult:
        self._maybe_fail("place_order")
        self._seq += 1
        result = OrderResult(
            order=order,
            broker_order_id=f"FAKE-{self._seq:06d}",
            status=OrderStatus.SUBMITTED,
            created_at=self._clock(),
        )

        price = self._prices.get((order.market, order.symbol))
        if price is None:
            return self._record(_reject(result, "시세가 없습니다"))

        if order.side is Side.BUY:
            needed = order.qty * (order.limit_price or price) * (1 + self._fee_rate)
            available = self._cash[order.market] - self._reserved_cash(order.market)
            if needed > available:
                return self._record(_reject(result, "주문 가능 금액이 부족합니다"))
        else:
            held, _ = self._holdings.get((order.market, order.symbol), (0, Decimal(0)))
            if order.qty > held - self._reserved_qty(order.market, order.symbol):
                return self._record(_reject(result, "매도 가능 수량이 부족합니다"))

        if _is_marketable(order, price):
            result = self._fill(result, price)
        return self._record(result)

    # ---- 내부 ----

    def _maybe_fail(self, method: str) -> None:
        if method in self.fail_on:
            raise BrokerError(f"fake: {method} 호출 실패(테스트용)")

    def _record(self, result: OrderResult) -> OrderResult:
        self._orders.append(result)
        return result

    def _open_orders(self, market: Market) -> Iterable[Order]:
        return (
            r.order
            for r in self._orders
            if r.status is OrderStatus.SUBMITTED and r.order.market == market
        )

    def _reserved_cash(self, market: Market) -> Decimal:
        return sum(
            (
                o.qty * (o.limit_price or Decimal(0)) * (1 + self._fee_rate)
                for o in self._open_orders(market)
                if o.side is Side.BUY
            ),
            Decimal(0),
        )

    def _reserved_qty(self, market: Market, symbol: str) -> int:
        return sum(
            o.qty for o in self._open_orders(market) if o.side is Side.SELL and o.symbol == symbol
        )

    def _fill(self, result: OrderResult, price: Decimal) -> OrderResult:
        order = result.order
        key = (order.market, order.symbol)
        amount = order.qty * price
        fee = amount * self._fee_rate
        qty, avg = self._holdings.get(key, (0, Decimal(0)))

        if order.side is Side.BUY:
            self._cash[order.market] -= amount + fee
            new_qty = qty + order.qty
            self._holdings[key] = (new_qty, (qty * avg + amount) / new_qty)
        else:
            self._cash[order.market] += amount - fee
            if qty == order.qty:
                del self._holdings[key]
            else:
                self._holdings[key] = (qty - order.qty, avg)

        return result.model_copy(
            update={
                "status": OrderStatus.FILLED,
                "filled_qty": order.qty,
                "avg_fill_price": price,
            }
        )


def _is_marketable(order: Order, price: Decimal) -> bool:
    if order.order_type is OrderType.MARKET:
        return True
    assert order.limit_price is not None
    if order.side is Side.BUY:
        return order.limit_price >= price
    return order.limit_price <= price


def _reject(result: OrderResult, message: str) -> OrderResult:
    return result.model_copy(update={"status": OrderStatus.REJECTED, "message": message})


def make_random_bars(
    n: int = 300,
    *,
    start_price: float = 10_000,
    start: str = "2025-01-02",
    drift: float = 0.0003,
    volatility: float = 0.02,
    decimals: int = 0,
    seed: int = 0,
) -> pd.DataFrame:
    """로컬 실행/테스트용 가짜 일봉(기하 랜덤워크). seed가 같으면 결과도 같다."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(start=start, periods=n)
    close = start_price * np.exp(np.cumsum(rng.normal(drift, volatility, n)))
    prev_close = np.concatenate([[start_price], close[:-1]])
    open_ = prev_close * (1 + rng.normal(0, volatility / 4, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, volatility / 2, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, volatility / 2, n)))
    volume = rng.integers(100_000, 1_000_000, n)
    return pd.DataFrame(
        {
            "open": open_.round(decimals),
            "high": high.round(decimals),
            "low": low.round(decimals),
            "close": close.round(decimals),
            "volume": volume,
        },
        index=index,
    )
