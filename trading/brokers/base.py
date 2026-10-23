"""증권사 어댑터 인터페이스. KIS, fake, 나중에 미래에셋이 이것을 구현한다."""

from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal

import pandas as pd

from trading.core.models import Balance, Holding, Market, Order, OrderResult, OrderType


class BrokerError(Exception):
    """증권사 통신 오류나 알 수 없는 응답. 엔진은 이 오류가 나면 주문하지 않고 알림만 보낸다."""


class UnsupportedOrderError(BrokerError):
    """그 시장에서 쓸 수 없는 주문 방식(예: KIS 해외 시장가)."""


class BrokerAdapter(ABC):
    name: str

    @abstractmethod
    def is_trading_day(self, market: Market, day: date) -> bool:
        """그 시장 현지 날짜 기준 개장일인지(주말/공휴일이면 False)."""

    @abstractmethod
    def supported_order_types(self, market: Market) -> frozenset[OrderType]:
        """그 시장에서 쓸 수 있는 주문 방식."""

    @abstractmethod
    def get_daily_bars(self, market: Market, symbol: str, count: int) -> pd.DataFrame:
        """최근 일봉 count개. 형식은 core.models.BAR_COLUMNS 참고."""

    @abstractmethod
    def get_price(self, market: Market, symbol: str) -> Decimal:
        """현재가."""

    @abstractmethod
    def get_balance(self, market: Market) -> Balance: ...

    @abstractmethod
    def get_holdings(self, market: Market) -> list[Holding]: ...

    @abstractmethod
    def get_orders(self, market: Market, day: date) -> list[OrderResult]:
        """그 시장 현지 날짜 기준으로 그날 낸 주문 목록."""

    def place_order(self, order: Order) -> OrderResult:
        """주문을 낸다.

        증권사가 주문을 거부하면 REJECTED 상태로 돌려주고,
        통신 오류나 알 수 없는 응답이면 BrokerError를 던진다.
        """
        if order.order_type not in self.supported_order_types(order.market):
            raise UnsupportedOrderError(
                f"{self.name}: {order.market} 시장에서는 {order.order_type} 주문을 쓸 수 없습니다"
            )
        return self._submit_order(order)

    @abstractmethod
    def _submit_order(self, order: Order) -> OrderResult: ...
