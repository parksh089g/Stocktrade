"""메모리 안에서만 동작하는 가짜 증권사. 테스트와 로컬 실행에 쓴다.

KIS와 같은 제약을 기본으로 흉내 낸다: 해외(US)는 지정가 주문만 받는다.
체결 규칙:
- 시장가: 현재가에 바로 체결
- 지정가: 현재가로 바로 체결할 수 있으면(매수 지정가 >= 현재가, 매도 지정가 <= 현재가)
  현재가에 체결, 아니면 접수 상태로 남는다. process_open_orders()로 다시 맞춰 본다.
- 부분 체결은 테스트에서 fill_order()로 직접 만든다.
- 주말과 holidays에 넣은 날은 휴장일이다.
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from trading.universe import Universe

Key = tuple[Market, str]


class FakeBroker(BrokerAdapter):
    name = "fake"

    def __init__(
        self,
        cash: Mapping[Market, Decimal | int | str] | None = None,
        *,
        market_order_markets: Iterable[Market] = (Market.KR,),
        fee_rate: Decimal = Decimal("0"),
        sell_tax_rate: Decimal = Decimal("0"),
        clock: Callable[[], datetime] = now_utc,
        holidays: Iterable[date] = (),
    ) -> None:
        cash = cash or {}
        self._cash: dict[Market, Decimal] = {m: Decimal(str(cash.get(m, 0))) for m in Market}
        self._market_order_markets = frozenset(market_order_markets)
        self._fee_rate = fee_rate
        self._sell_tax_rate = sell_tax_rate
        self._clock = clock
        self.holidays: set[date] = set(holidays)
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
        """미체결 지정가 주문을 현재가와 다시 맞춰 본다."""
        for i, result in enumerate(self._orders):
            if result.status.is_open:
                price = self._prices.get((result.order.market, result.order.symbol))
                if price is not None and _is_marketable(result.order, price):
                    self._orders[i] = self._fill(result, price)

    def touch_open_orders(self, market: Market, symbol: str, high: Decimal, low: Decimal) -> None:
        """하루 동안 가격이 high~low 사이를 움직였을 때 지정가에 닿은 미체결 주문을
        지정가로 체결한다.

        백테스트에서 일봉의 고가/저가로 장중 체결을 흉내 낼 때 쓴다.
        """
        for i, result in enumerate(self._orders):
            o = result.order
            if not result.status.is_open or o.market != market or o.symbol != symbol:
                continue
            if o.limit_price is None:
                continue
            touched = o.limit_price >= low if o.side is Side.BUY else o.limit_price <= high
            if touched:
                self._orders[i] = self._fill(result, o.limit_price)

    def expire_open_orders(self, market: Market) -> None:
        """장 마감: 남은 미체결 주문을 만료시킨다."""
        for i, result in enumerate(self._orders):
            if result.status.is_open and result.order.market == market:
                self._orders[i] = result.model_copy(update={"status": OrderStatus.EXPIRED})

    def fill_order(
        self, broker_order_id: str, qty: int | None = None, price: Decimal | int | str | None = None
    ) -> None:
        """미체결 주문을 강제로 체결한다(qty를 주면 그만큼만 부분 체결). 테스트용."""
        for i, result in enumerate(self._orders):
            if result.broker_order_id == broker_order_id:
                if not result.status.is_open:
                    raise ValueError(f"{broker_order_id}는 미체결 주문이 아닙니다")
                fill_price = (
                    Decimal(str(price))
                    if price is not None
                    else (
                        result.order.limit_price
                        or self._prices[(result.order.market, result.order.symbol)]
                    )
                )
                self._orders[i] = self._fill(result, fill_price, qty)
                return
        raise KeyError(broker_order_id)

    # ---- BrokerAdapter ----

    def is_trading_day(self, market: Market, day: date) -> bool:
        self._maybe_fail("is_trading_day")
        return day.weekday() < 5 and day not in self.holidays

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

    def _open_orders(self, market: Market) -> Iterable[tuple[Order, int]]:
        """(주문, 남은 수량)"""
        return (
            (r.order, r.order.qty - r.filled_qty)
            for r in self._orders
            if r.status.is_open and r.order.market == market
        )

    def _reserved_cash(self, market: Market) -> Decimal:
        return sum(
            (
                left * (o.limit_price or Decimal(0)) * (1 + self._fee_rate)
                for o, left in self._open_orders(market)
                if o.side is Side.BUY
            ),
            Decimal(0),
        )

    def _reserved_qty(self, market: Market, symbol: str) -> int:
        return sum(
            left
            for o, left in self._open_orders(market)
            if o.side is Side.SELL and o.symbol == symbol
        )

    def _fill(self, result: OrderResult, price: Decimal, qty: int | None = None) -> OrderResult:
        order = result.order
        fill_qty = qty if qty is not None else order.qty - result.filled_qty
        if not 0 < fill_qty <= order.qty - result.filled_qty:
            raise ValueError(f"체결 수량이 잘못됐습니다: {fill_qty}")
        key = (order.market, order.symbol)
        amount = fill_qty * price
        fee = amount * self._fee_rate
        held, avg = self._holdings.get(key, (0, Decimal(0)))

        if order.side is Side.BUY:
            self._cash[order.market] -= amount + fee
            new_held = held + fill_qty
            self._holdings[key] = (new_held, (held * avg + amount) / new_held)
        else:
            self._cash[order.market] += amount - fee - amount * self._sell_tax_rate
            if held == fill_qty:
                del self._holdings[key]
            else:
                self._holdings[key] = (held - fill_qty, avg)

        filled = result.filled_qty + fill_qty
        prev_amount = result.filled_qty * (result.avg_fill_price or Decimal(0))
        return result.model_copy(
            update={
                "status": OrderStatus.FILLED
                if filled == order.qty
                else OrderStatus.PARTIALLY_FILLED,
                "filled_qty": filled,
                "avg_fill_price": (prev_amount + amount) / filled,
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
    end: str | None = None,
    drift: float = 0.0003,
    volatility: float = 0.02,
    decimals: int = 0,
    seed: int = 0,
) -> pd.DataFrame:
    """로컬 실행/테스트용 가짜 일봉(기하 랜덤워크). seed가 같으면 결과도 같다.

    end를 주면 그날로 끝나게, 아니면 start부터 n일.
    """
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(end=end, periods=n) if end else pd.bdate_range(start=start, periods=n)
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


def make_demo_broker(
    universe: "Universe", *, end: date, seed: int = 0, clock: Callable[[], datetime] = now_utc
) -> FakeBroker:
    """로컬 실행용: 목록의 종목마다 end까지의 가짜 일봉과 현금을 넣은 fake 증권사."""
    broker = FakeBroker(cash={Market.KR: 10_000_000, Market.US: 10_000}, clock=clock)
    for market, instruments in universe.items():
        start_price, decimals = (50_000, 0) if market is Market.KR else (150, 2)
        for i, inst in enumerate(instruments):
            bars = make_random_bars(
                200,
                start_price=start_price,
                end=end.isoformat(),
                decimals=decimals,
                seed=seed * 1000 + i,
            )
            broker.set_bars(market, inst.symbol, bars)
    return broker
