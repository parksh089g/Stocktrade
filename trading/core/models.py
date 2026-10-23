"""엔진, 전략, 증권사 어댑터가 함께 쓰는 공통 모델.

금액과 가격은 Decimal, 수량은 정수 주식 수, 시간은 UTC로 둔다.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from trading.core.clock import KST, NEW_YORK, ensure_utc, now_utc

UtcDatetime = Annotated[datetime, AfterValidator(ensure_utc)]
Price = Annotated[Decimal, Field(gt=0)]
Qty = Annotated[int, Field(gt=0)]

# 일봉 DataFrame 형식: 인덱스는 날짜(DatetimeIndex, 오름차순), 컬럼은 아래 다섯 개
BAR_COLUMNS = ("open", "high", "low", "close", "volume")


class Market(StrEnum):
    KR = "KR"
    US = "US"

    @property
    def currency(self) -> str:
        return "KRW" if self is Market.KR else "USD"

    @property
    def timezone(self) -> ZoneInfo:
        return KST if self is Market.KR else NEW_YORK

    def format_price(self, value: Decimal | float) -> str:
        """알림/로그용 가격 표기. 예: KR 70,000 / US 180.50"""
        return f"{value:,.0f}" if self is Market.KR else f"{value:,.2f}"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(StrEnum):
    SUBMITTED = "SUBMITTED"  # 접수됨, 아직 체결 전
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Signal(_Frozen):
    """전략이 내는 매수/매도 신호. 수량은 리스크 단계에서 정한다."""

    market: Market
    symbol: str
    side: Side
    strategy: str
    price: Price  # 신호 기준 가격(보통 마지막 종가)
    bar_date: date  # 신호를 낸 일봉 날짜(그 시장 현지 날짜)
    reason: str
    created_at: UtcDatetime = Field(default_factory=now_utc)


class Order(_Frozen):
    """증권사에 보낼 주문."""

    market: Market
    symbol: str
    side: Side
    qty: Qty
    order_type: OrderType
    limit_price: Price | None = None
    strategy: str
    client_order_id: str

    @model_validator(mode="after")
    def _check_price(self) -> "Order":
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("지정가 주문에는 limit_price가 필요합니다")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("시장가 주문에는 limit_price를 넣지 않습니다")
        return self


class OrderResult(_Frozen):
    """증권사가 알려 준 주문 상태."""

    order: Order
    broker_order_id: str | None
    status: OrderStatus
    filled_qty: int = Field(default=0, ge=0)
    avg_fill_price: Decimal | None = None
    message: str = ""
    created_at: UtcDatetime = Field(default_factory=now_utc)


class Holding(_Frozen):
    """증권사 계좌에 실제로 있는 종목(전략 구분 없음)."""

    market: Market
    symbol: str
    qty: Qty
    avg_price: Price


class Position(_Frozen):
    """전략별 보유분. 어느 전략이 산 것인지 기록한다."""

    market: Market
    symbol: str
    strategy: str
    qty: Qty
    avg_price: Price
    opened_at: UtcDatetime


class Balance(_Frozen):
    """시장(통화)별 계좌 잔고."""

    market: Market
    cash: Decimal = Field(ge=0)  # 주문 가능 현금
    total_equity: Decimal = Field(ge=0)  # 현금 + 보유 종목 평가액

    @property
    def currency(self) -> str:
        return self.market.currency


def validate_bars(df: pd.DataFrame) -> pd.DataFrame:
    """일봉 DataFrame 형식을 확인한다. 문제가 있으면 ValueError."""
    missing = [c for c in BAR_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"일봉 데이터에 컬럼이 없습니다: {missing}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("일봉 데이터의 인덱스는 DatetimeIndex여야 합니다")
    if not df.index.is_monotonic_increasing or df.index.has_duplicates:
        raise ValueError("일봉 날짜는 중복 없이 오름차순이어야 합니다")
    return df
