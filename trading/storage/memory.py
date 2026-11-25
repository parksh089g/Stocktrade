"""메모리 저장소. 테스트와 로컬 실행용(프로그램이 끝나면 사라진다)."""

from datetime import date

from trading.core.clock import now_utc, trading_date
from trading.core.models import Market, OrderResult, Position
from trading.engine.report import RunReport
from trading.storage.base import EngineConfig, PauseState, PushSubscription, Store, StoreError


class MemoryStore(Store):
    def __init__(self) -> None:
        self._pause = PauseState()
        self._positions: dict[tuple[Market, str, str], Position] = {}
        self._orders: dict[str, OrderResult] = {}
        self.runs: list[RunReport] = []
        self.summaries: list[str] = []
        self.subscriptions: dict[str, PushSubscription] = {}
        self.cache: dict[str, str] = {}
        self.engine_config = EngineConfig()
        # 메서드 이름을 넣으면 그 메서드가 StoreError를 던진다 (오류 상황 테스트용)
        self.fail_on: set[str] = set()

    def _maybe_fail(self, method: str) -> None:
        if method in self.fail_on:
            raise StoreError(f"memory: {method} 실패(테스트용)")

    def get_pause(self) -> PauseState:
        self._maybe_fail("get_pause")
        return self._pause

    def set_pause(self, paused: bool, reason: str = "") -> None:
        self._maybe_fail("set_pause")
        self._pause = PauseState(paused=paused, reason=reason, updated_at=now_utc())

    def get_positions(self, market: Market) -> list[Position]:
        self._maybe_fail("get_positions")
        return [p for (m, _, _), p in self._positions.items() if m == market]

    def upsert_position(self, position: Position) -> None:
        self._maybe_fail("upsert_position")
        self._positions[(position.market, position.symbol, position.strategy)] = position

    def delete_position(self, market: Market, symbol: str, strategy: str) -> None:
        self._maybe_fail("delete_position")
        self._positions.pop((market, symbol, strategy), None)

    def get_orders(self, market: Market, day: date) -> list[OrderResult]:
        self._maybe_fail("get_orders")
        return [
            r
            for r in self._orders.values()
            if r.order.market == market and trading_date(market, r.created_at) == day
        ]

    def all_orders(self) -> list[OrderResult]:
        """모든 주문 기록. 백테스트 집계용."""
        return list(self._orders.values())

    def get_open_orders(self, market: Market) -> list[OrderResult]:
        self._maybe_fail("get_open_orders")
        return [r for r in self._orders.values() if r.order.market == market and r.status.is_open]

    def save_order(self, result: OrderResult) -> None:
        self._maybe_fail("save_order")
        self._orders[result.order.client_order_id] = result

    def save_run(self, report: RunReport, summary: str) -> str | None:
        self._maybe_fail("save_run")
        self.runs.append(report)
        self.summaries.append(summary)
        return str(len(self.runs))

    def add_push_subscription(self, subscription: PushSubscription) -> None:
        """웹앱이 하는 일을 대신한다. 테스트용."""
        self.subscriptions[subscription.endpoint] = subscription

    def get_push_subscriptions(self) -> list[PushSubscription]:
        self._maybe_fail("get_push_subscriptions")
        return list(self.subscriptions.values())

    def delete_push_subscription(self, endpoint: str) -> None:
        self._maybe_fail("delete_push_subscription")
        self.subscriptions.pop(endpoint, None)

    def get_cache(self, name: str) -> str | None:
        self._maybe_fail("get_cache")
        return self.cache.get(name)

    def put_cache(self, name: str, value: str) -> None:
        self._maybe_fail("put_cache")
        self.cache[name] = value

    def delete_cache(self, name: str) -> None:
        self._maybe_fail("delete_cache")
        self.cache.pop(name, None)

    def get_engine_config(self) -> EngineConfig:
        self._maybe_fail("get_engine_config")
        return self.engine_config
