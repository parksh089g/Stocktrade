"""기록 저장소 인터페이스. 운영은 Supabase(supabase.py), 테스트와 로컬은 메모리(memory.py).

저장하는 것: 일시정지 스위치, 전략별 보유분, 주문 기록, 실행 기록, 웹 푸시 구독, 엔진 캐시,
웹앱에서 넣은 엔진 설정과 비밀값(LLM 기능).
보유분과 주문은 모의투자/실전 계좌가 섞이지 않도록 모드별로 따로 둔다.
"""

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from trading.core.models import Market, OrderResult, Position, UtcDatetime
from trading.engine.report import RunReport


class StoreError(Exception):
    """저장소 읽기/쓰기 실패."""


class PauseState(BaseModel):
    paused: bool = False
    reason: str = ""
    updated_at: UtcDatetime | None = None


class PushSubscription(BaseModel):
    """웹앱이 등록한 기기 하나의 푸시 구독."""

    endpoint: str
    p256dh: str
    auth: str


class EngineConfig(BaseModel):
    """웹앱 설정 화면에서 넣은 엔진 설정(engine_settings 표)과 비밀값(Supabase Vault)."""

    settings: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)


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
    def save_run(self, report: RunReport, summary: str) -> str | None:
        """실행 기록을 남기고 그 id를 돌려준다. summary는 알림함에 보여 줄 전체 문장."""

    @abstractmethod
    def get_push_subscriptions(self) -> list[PushSubscription]: ...

    @abstractmethod
    def delete_push_subscription(self, endpoint: str) -> None:
        """만료된 구독을 지운다."""

    @abstractmethod
    def get_cache(self, name: str) -> str | None:
        """엔진만 쓰는 값(예: 암호화한 증권사 토큰). 웹앱에서는 보이지 않는다."""

    @abstractmethod
    def put_cache(self, name: str, value: str) -> None: ...

    @abstractmethod
    def delete_cache(self, name: str) -> None: ...

    def get_engine_config(self) -> EngineConfig:
        """웹앱에서 넣은 엔진 설정과 비밀값. 기본은 비어 있다(Supabase만 구현)."""
        return EngineConfig()
