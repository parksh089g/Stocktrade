"""기록 저장소 인터페이스. M4에서 Supabase로 구현한다.

저장하는 것: 일시정지 스위치, 전략별 보유분, 주문 기록, 실행 기록.
"""

from abc import ABC, abstractmethod
from datetime import date

from pydantic import BaseModel

from trading.core.models import Market, OrderResult, Position, UtcDatetime
from trading.engine.report import RunReport


class StoreError(Exception):
    """저장소 읽기/쓰기 실패."""


class PauseState(BaseModel):
    paused: bool = False
    reason: str = ""
    updated_at: UtcDatetime | None = None


class Store(ABC):
    @abstractmethod
    def get_pause(self) -> PauseState: ...

    @abstractmethod
    def set_pause(self, paused: bool, reason: str = "") -> None: ...

    @abstractmethod
    def get_positions(self, market: Market) -> list[Position]: ...

    @abstractmethod
    def upsert_position(self, position: Position) -> None:
        """(시장, 종목, 전략)이 같은 보유분을 덮어쓰거나 새로 넣는다."""

    @abstractmethod
    def delete_position(self, market: Market, symbol: str, strategy: str) -> None: ...

    @abstractmethod
    def get_orders(self, market: Market, day: date) -> list[OrderResult]:
        """그 시장 현지 날짜 기준으로 그날 낸 주문."""

    @abstractmethod
    def get_open_orders(self, market: Market) -> list[OrderResult]:
        """아직 체결이 더 될 수 있는(접수/부분체결) 주문."""

    @abstractmethod
    def save_order(self, result: OrderResult) -> None:
        """client_order_id가 같은 주문을 덮어쓰거나 새로 넣는다."""

    @abstractmethod
    def save_run(self, report: RunReport) -> None: ...
