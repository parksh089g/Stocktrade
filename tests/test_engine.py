"""하루 실행 흐름을 fake 증권사와 메모리 저장소로 끝까지 돌려 본다."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from tests.helpers import Clock, StrategyA, StrategyB, bars_from_closes
from trading.brokers.base import BrokerError
from trading.brokers.fake import FakeBroker
from trading.core.clock import KST
from trading.core.models import (
    Instrument,
    Market,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
)
from trading.engine.report import RunStatus
from trading.engine.runner import Engine
from trading.notify.base import MemoryNotifier
from trading.risk.config import RiskConfig
from trading.risk.planner import ALREADY_ORDERED
from trading.storage.memory import MemoryStore

D = Decimal
KR = Market.KR
FRI = date(2026, 10, 23)
MON = date(2026, 10, 26)
MON_0840 = datetime(2026, 10, 26, 8, 40, tzinfo=KST)
NAMES = {"005930": "삼성전자", "000660": "SK하이닉스", "035420": "NAVER"}


@dataclass
class Env:
    engine: Engine
    broker: FakeBroker
    store: MemoryStore
    notifier: MemoryNotifier
    clock: Clock
    a: StrategyA
    b: StrategyB

    def run(self):
        return self.engine.run(KR)

    @property
    def message(self) -> str:
        return self.notifier.messages[-1]

    def broker_orders(self, day: date = MON):
        return self.broker.get_orders(KR, day)

    def next_day(self, days: int = 1) -> None:
        self.clock.now += timedelta(days=days)


def make_env(
    prices=None,
    *,
    universe=None,
    bars_end=FRI,
    store=None,
    cash=10_000_000,
    **engine_kw,
) -> Env:
    prices = prices or {"005930": 70_000, "000660": 200_000}
    clock = Clock(MON_0840)
    broker = FakeBroker(cash={KR: cash, Market.US: 10_000}, clock=clock)
    for symbol, price in prices.items():
        broker.set_bars(KR, symbol, bars_from_closes([price] * 10, end=bars_end))
    symbols = universe if universe is not None else list(prices)
    a, b = StrategyA(), StrategyB()
    store = store or MemoryStore()
    notifier = MemoryNotifier()
    engine = Engine(
        broker=broker,
        store=store,
        notifier=notifier,
        strategies=[a, b],
        universe={
            KR: [Instrument(market=KR, symbol=s, name=NAMES.get(s, "")) for s in symbols],
            Market.US: [],
        },
        risk=RiskConfig(strategy_weights={"a": D("0.5"), "b": D("0.5")}),
        clock=clock,
        **engine_kw,
    )
    return Env(engine, broker, store, notifier, clock, a, b)


@pytest.fixture
def env() -> Env:
    return make_env()


# ---- 기본 흐름 ----


def test_no_signal_run(env: Env):
    report = env.run()
    assert report.status is RunStatus.OK
    assert report.orders == []
    assert "신호가 없습니다" in env.message
    assert env.store.runs == [report]


def test_buy_signal_places_limit_order(env: Env):
    env.a.signals = {"005930": Side.BUY}
    report = env.run()

    assert report.status is RunStatus.OK
    [result] = report.orders
    o = result.order
    assert (o.side, o.order_type, o.limit_price, o.qty) == (Side.BUY, OrderType.LIMIT, 70_700, 17)
    assert len(env.broker_orders()) == 1
    assert env.store.get_orders(KR, MON)[0].broker_order_id == result.broker_order_id
    assert "매수 삼성전자(005930) 17주 지정가 70,700 [a]" in env.message
    assert "08:40 KST" in env.message


def test_full_cycle_buy_fill_then_sell(env: Env):
    # 1일차: 매수 주문. 시가가 높게 떠서 바로 체결되지 않음
    env.a.signals = {"005930": Side.BUY}
    env.broker.set_price(KR, "005930", 72_000)
    first = env.run()
    assert first.orders[0].status is OrderStatus.SUBMITTED
    assert env.store.get_positions(KR) == []

    # 장중에 가격이 내려와 체결
    env.broker.set_price(KR, "005930", 70_500)
    env.broker.process_open_orders()

    # 2일차: 실행 처음에 체결을 확인해 전략 a 보유분으로 기록
    env.a.signals = {}
    env.next_day()
    second = env.run()
    assert [(f.qty, f.price) for f in second.fills] == [(17, D(70_500))]
    [pos] = env.store.get_positions(KR)
    assert (pos.strategy, pos.qty, pos.avg_price) == ("a", 17, D(70_500))
    assert "새 체결" in env.message
    # 전략에는 자기 보유분이 넘어간다
    assert any(p is not None and p.qty == 17 for s, _, p in env.a.seen if s == "005930")
    assert all(p is None for _, _, p in env.b.seen)

    # 3일차: 매도 신호 → 보유 수량 전부 매도, 바로 체결되어 보유분 삭제
    env.a.signals = {"005930": Side.SELL}
    env.next_day()
    third = env.run()
    [sell] = third.orders
    assert (sell.order.side, sell.order.qty) == (Side.SELL, 17)
    assert sell.status is OrderStatus.FILLED
    assert env.store.get_positions(KR) == []
    assert third.warnings == []


def test_unfilled_order_expires_next_day(env: Env):
    env.a.signals = {"005930": Side.BUY}
    env.broker.set_price(KR, "005930", 72_000)
    env.run()

    env.a.signals = {}
    env.next_day()
    env.run()
    [rec] = env.store.get_orders(KR, MON)
    assert rec.status is OrderStatus.EXPIRED
    assert env.store.get_positions(KR) == []


# ---- 중복 주문 ----


def test_second_run_same_day_does_not_order_same_symbol(env: Env):
    env.a.signals = {"005930": Side.BUY}
    env.broker.set_price(KR, "005930", 72_000)
    env.run()

    env.a.signals = {}
    env.b.signals = {"005930": Side.BUY}
    report = env.run()
    assert report.orders == []
    assert report.skipped[0].reason == ALREADY_ORDERED
    assert len(env.broker_orders()) == 1


def test_order_known_only_to_broker_blocks_symbol(env: Env):
    # 저장소에는 없지만 증권사에 오늘 주문이 있다(예: 기록 저장 실패, 수동 주문)
    env.broker.place_order(
        Order(
            market=KR,
            symbol="005930",
            side=Side.BUY,
            qty=1,
            order_type=OrderType.LIMIT,
            limit_price=D(60_000),
            strategy="manual",
            client_order_id="manual",
        )
    )
    env.a.signals = {"005930": Side.BUY}
    report = env.run()
    assert report.orders == []
    assert report.skipped[0].reason == ALREADY_ORDERED


def test_two_strategies_same_symbol_one_order(env: Env):
    env.a.signals = {"005930": Side.BUY}
    env.b.signals = {"005930": Side.BUY}
    report = env.run()
    assert [r.order.strategy for r in report.orders] == ["a"]
    assert len(env.broker_orders()) == 1


# ---- 일시정지, 휴장, 장 마감 ----


def test_paused_places_no_orders_but_still_syncs(env: Env):
    env.a.signals = {"005930": Side.BUY}
    env.broker.set_price(KR, "005930", 72_000)
    env.run()
    env.broker.set_price(KR, "005930", 70_000)
    env.broker.process_open_orders()

    env.store.set_pause(True, "폰에서 정지")
    env.next_day()
    report = env.run()
    assert report.status is RunStatus.PAUSED
    assert report.orders == []
    assert len(report.fills) == 1  # 체결 확인은 한다
    assert "일시정지 - 폰에서 정지" in env.message
    assert len(env.broker.get_orders(KR, MON + timedelta(days=1))) == 0


def test_pause_turned_on_during_orders_stops_remaining():
    class PauseAfterFirstOrder(MemoryStore):
        def save_order(self, result):
            super().save_order(result)
            self.set_pause(True, "폰에서 정지")

    env = make_env(store=PauseAfterFirstOrder())
    env.a.signals = {"005930": Side.BUY, "000660": Side.BUY}
    report = env.run()
    assert len(report.orders) == 1
    assert len(env.broker_orders()) == 1
    assert report.status is RunStatus.PAUSED
    assert any("남은 주문 1건" in w for w in report.warnings)


def test_holiday_is_skipped(env: Env):
    env.broker.holidays.add(MON)
    env.a.signals = {"005930": Side.BUY}
    report = env.run()
    assert report.status is RunStatus.SKIPPED
    assert "휴장일" in report.message
    assert env.broker_orders() == []


def test_weekend_is_skipped(env: Env):
    env.clock.now = datetime(2026, 10, 24, 8, 40, tzinfo=KST)  # 토요일
    env.a.signals = {"005930": Side.BUY}
    assert env.run().status is RunStatus.SKIPPED


def test_after_close_is_skipped(env: Env):
    env.clock.now = datetime(2026, 10, 26, 15, 30, tzinfo=KST)
    env.a.signals = {"005930": Side.BUY}
    report = env.run()
    assert report.status is RunStatus.SKIPPED
    assert "장 마감" in report.message
    assert env.broker_orders() == []


def test_market_hours_check_can_be_disabled():
    env = make_env(check_market_hours=False)
    env.broker.holidays.add(MON)
    env.a.signals = {"005930": Side.BUY}
    assert len(env.run().orders) == 1


# ---- 오류 ----


def test_pause_read_error_means_no_orders(env: Env):
    env.store.fail_on.add("get_pause")
    env.a.signals = {"005930": Side.BUY}
    report = env.run()
    assert report.status is RunStatus.ERROR
    assert "주문하지 않았습니다" in report.error
    assert env.broker_orders() == []
    assert "상태: 오류" in env.message


@pytest.mark.parametrize("method", ["get_balance", "get_holdings", "get_orders"])
def test_account_errors_mean_no_orders(env: Env, method):
    env.broker.fail_on.add(method)
    env.a.signals = {"005930": Side.BUY}
    report = env.run()
    assert report.status is RunStatus.ERROR
    env.broker.fail_on.clear()
    assert env.broker_orders() == []


def test_one_symbol_data_error_skips_only_that_symbol():
    env = make_env(universe=["005930", "000660", "999999"])  # 999999는 일봉 없음
    env.a.signals = {"005930": Side.BUY, "999999": Side.BUY}
    report = env.run()
    assert report.status is RunStatus.OK
    assert [r.order.symbol for r in report.orders] == ["005930"]
    assert any("999999" in w for w in report.warnings)


def test_order_error_stops_remaining_orders(monkeypatch):
    env = make_env({"005930": 70_000, "000660": 200_000, "035420": 200_000})
    calls = []
    submit = env.broker._submit_order

    def flaky(order):
        calls.append(order.symbol)
        if len(calls) == 2:
            raise BrokerError("timeout")
        return submit(order)

    monkeypatch.setattr(env.broker, "_submit_order", flaky)
    env.a.signals = {"005930": Side.BUY, "000660": Side.BUY}
    env.b.signals = {"035420": Side.BUY}
    report = env.run()

    assert report.status is RunStatus.ERROR
    assert calls == ["005930", "000660"]  # 035420은 시도하지 않는다
    assert len(report.orders) == 1
    assert "증권사 앱에서 확인" in report.error
    assert len(env.store.get_orders(KR, MON)) == 1


def test_stale_bars_are_skipped():
    env = make_env(bars_end=date(2026, 10, 14))
    env.a.signals = {"005930": Side.BUY}
    report = env.run()
    assert report.orders == []
    assert any("오래돼" in w for w in report.warnings)
    assert env.a.seen == []


def test_todays_unfinished_bar_is_not_used():
    env = make_env(bars_end=MON)  # 오늘 봉이 섞여 들어온 경우
    env.run()
    assert {d for _, d, _ in env.a.seen} == {FRI}


def test_errors_and_notifications_hide_secrets(env: Env, monkeypatch):
    env.engine.secrets = ["SECRET-TOKEN-123"]

    def boom(market):
        raise BrokerError("auth failed token=SECRET-TOKEN-123")

    monkeypatch.setattr(env.broker, "get_balance", boom)
    report = env.run()
    assert "SECRET-TOKEN-123" not in report.error
    assert "SECRET-TOKEN-123" not in env.message
    assert "***" in env.message


def test_notifier_and_store_failures_do_not_crash(env: Env, monkeypatch):
    def broken(text):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(env.notifier, "send", broken)
    env.store.fail_on.add("save_run")
    report = env.run()
    assert report.status is RunStatus.OK
    assert report.notify_failed
    assert any("실행 기록 저장 실패" in w for w in report.warnings)


# ---- 보유 종목 관리 ----


def test_held_symbol_outside_universe_can_only_be_sold():
    env = make_env({"005930": 70_000, "035420": 200_000}, universe=["005930"])
    env.store.upsert_position(
        Position(
            market=KR,
            symbol="035420",
            strategy="a",
            qty=5,
            avg_price=D(190_000),
            opened_at=datetime(2026, 10, 1, tzinfo=KST),
        )
    )
    env.broker.set_holding(KR, "035420", 5, 190_000)
    env.a.signals = {"035420": Side.SELL}
    env.b.signals = {"035420": Side.BUY}
    report = env.run()

    assert [(r.order.symbol, r.order.side) for r in report.orders] == [("035420", Side.SELL)]
    assert all(s != "035420" for s, _, _ in env.b.seen)


def test_mismatch_between_records_and_account_is_warned(env: Env):
    env.broker.set_holding(KR, "005930", 10, 60_000)
    report = env.run()
    assert "005930: 전략 기록 0주, 계좌 10주로 다릅니다" in report.warnings
    assert "확인 필요" in env.message


def test_daily_buy_cap():
    env = make_env(daily_buy_caps={KR: D(500_000)})
    env.a.signals = {"005930": Side.BUY}
    [result] = env.run().orders
    assert result.order.qty == 7  # 500,000 / (70,700 × 1.003)
