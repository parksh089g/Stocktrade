"""하루 실행 흐름을 fake 증권사와 메모리 저장소로 끝까지 돌려 본다."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from tests.fake_postgrest import FakePostgrest
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
from trading.storage.base import StoreError
from trading.storage.memory import MemoryStore
from trading.storage.supabase import SupabaseStore
from trading.strategies.trend_ma import TrendMA

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
        return self.notifier.messages[-1].text

    @property
    def notification(self):
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
    assert env.notification.url == "/runs/1"
    assert not env.notification.urgent


def test_buy_signal_places_limit_order(env: Env):
    env.a.signals = {"005930": Side.BUY}
    report = env.run()

    assert report.status is RunStatus.OK
    [result] = report.orders
    o = result.order
    assert (o.side, o.order_type, o.limit_price, o.qty) == (Side.BUY, OrderType.LIMIT, 70_700, 17)
    assert len(env.broker_orders()) == 1
    assert env.store.get_orders(KR, MON)[0].broker_order_id == result.broker_order_id
    assert env.notification.title == "[모의투자] 국내 · 완료"
    assert "매수 삼성전자(005930) 17주 지정가 70,700 [a]" in env.message
    # 알림함 기록에는 한국 시간이 들어간다
    assert env.store.summaries[-1].startswith("[모의투자] 국내 · 완료 · 2026-10-26 08:40 KST")


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
    assert env.notification.title == "[모의투자] 국내 · 일시정지"
    assert "폰에서 정지 (손절 매도만 냅니다)" in env.message
    assert len(env.broker.get_orders(KR, MON + timedelta(days=1))) == 0


class PauseAfterFirstOrder(MemoryStore):
    def save_order(self, result):
        super().save_order(result)
        self.set_pause(True, "폰에서 정지")


def test_pause_turned_on_during_orders_stops_remaining():
    env = make_env(store=PauseAfterFirstOrder())
    env.a.signals = {"005930": Side.BUY, "000660": Side.BUY}
    report = env.run()
    assert len(report.orders) == 1
    assert len(env.broker_orders()) == 1
    assert report.status is RunStatus.PAUSED
    assert "주문 도중 일시정지가 켜졌습니다" in report.warnings
    assert [s.signal.symbol for s in report.skipped] == ["000660"]


def hold(env: Env, symbol: str, strategy: str = "a", qty: int = 5, avg: int = 80_000) -> None:
    """전략 보유분과 계좌 보유를 함께 만든다."""
    env.store.upsert_position(
        Position(
            market=KR,
            symbol=symbol,
            strategy=strategy,
            qty=qty,
            avg_price=D(avg),
            opened_at=datetime(2026, 10, 1, tzinfo=KST),
        )
    )
    env.broker.set_holding(KR, symbol, qty, avg)


def test_stop_loss_sell_goes_out_while_paused(env: Env):
    hold(env, "005930")
    hold(env, "000660", strategy="b", avg=220_000)
    env.store.set_pause(True, "점검 중")
    env.a.signals = {"005930": Side.SELL}
    env.a.stop_loss = {"005930"}
    env.b.signals = {"000660": Side.SELL}  # 손절이 아닌 일반 매도
    report = env.run()

    assert report.status is RunStatus.PAUSED
    assert [(r.order.symbol, r.order.side) for r in report.orders] == [("005930", Side.SELL)]
    [skipped] = report.skipped
    assert skipped.signal.symbol == "000660"
    assert "일시정지" in skipped.reason
    assert "점검 중 (손절 매도만 냅니다)" in env.message


def test_buys_are_not_placed_while_paused(env: Env):
    env.store.set_pause(True)
    env.a.signals = {"005930": Side.BUY}
    report = env.run()
    assert report.orders == []
    assert env.broker_orders() == []
    assert "일시정지" in report.skipped[0].reason


def test_stop_loss_continues_when_pause_turned_on_mid_run():
    env = make_env(store=PauseAfterFirstOrder())
    hold(env, "000660")
    env.a.signals = {"005930": Side.BUY, "000660": Side.SELL}
    env.a.stop_loss = {"000660"}
    report = env.run()
    # 매도(손절)가 먼저 나가고, 그 뒤 켜진 일시정지에 매수는 막힌다
    assert [(r.order.symbol, r.order.side) for r in report.orders] == [("000660", Side.SELL)]
    assert [s.signal.symbol for s in report.skipped] == ["005930"]


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
    assert env.notification.title == "[모의투자] 국내 · 오류"
    assert env.notification.urgent


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
    assert any("거래일치가 없어" in w for w in report.warnings)
    assert env.a.seen == []


def test_long_holiday_is_not_stale():
    # 2025년 추석: 10/3~10/9 휴장 → 10/10 아침의 마지막 일봉은 10/2(달력으로 8일 전)
    env = make_env(bars_end=date(2025, 10, 2))
    env.clock.now = datetime(2025, 10, 10, 8, 40, tzinfo=KST)
    env.broker.holidays.update(date(2025, 10, d) for d in (3, 6, 7, 8, 9))
    env.a.signals = {"005930": Side.BUY}
    report = env.run()
    assert report.warnings == []
    assert len(report.orders) == 1


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
    assert env.notification.urgent


def test_daily_buy_cap():
    env = make_env(daily_buy_caps={KR: D(500_000)})
    env.a.signals = {"005930": Side.BUY}
    [result] = env.run().orders
    assert result.order.qty == 7  # 500,000 / (70,700 × 1.003)


# ---- 기록 저장 실패, Supabase 저장소 ----


def test_order_placed_but_record_failed_stops_and_warns():
    class FailOnSecondSave(MemoryStore):
        saves = 0

        def save_order(self, result):
            FailOnSecondSave.saves += 1
            if FailOnSecondSave.saves == 2:
                raise StoreError("db down")
            super().save_order(result)

    env = make_env(store=FailOnSecondSave())
    env.a.signals = {"005930": Side.BUY, "000660": Side.BUY}
    report = env.run()

    assert report.status is RunStatus.ERROR
    assert "증권사에 냈지만 기록 저장에 실패" in report.error
    assert "주문하지 않았습니다" not in report.error
    assert len(env.broker_orders()) == 1  # 두 번째 주문은 내지 않았다
    assert len(report.orders) == 1


def test_full_cycle_with_supabase_store():
    fake = FakePostgrest()
    store = SupabaseStore(
        "https://test.supabase.co",
        fake.key,
        "paper",
        transport=fake.transport,
        sleep=lambda _: None,
    )
    env = make_env(store=store)

    env.a.signals = {"005930": Side.BUY}
    env.broker.set_price(KR, "005930", 72_000)
    env.run()
    env.broker.set_price(KR, "005930", 70_500)
    env.broker.process_open_orders()

    env.a.signals = {}
    env.next_day()
    env.run()
    [pos] = fake.tables["positions"]
    assert (pos["mode"], pos["strategy"], pos["qty"]) == ("paper", "a", 17)

    env.a.signals = {"005930": Side.SELL}
    env.next_day()
    report = env.run()
    assert report.status is RunStatus.OK
    assert fake.tables["positions"] == []
    assert [o["status"] for o in fake.tables["orders"]] == ["FILLED", "FILLED"]
    assert len(fake.tables["runs"]) == 3
    assert env.notification.url == "/runs/3"


# ---- 시장 지수 일봉 넘기기 ----


def test_engine_passes_index_bars_to_strategies(env: Env):
    env.broker.set_bars(KR, "069500", bars_from_closes(list(range(1, 11)), end=MON))
    env.engine.index_symbols = {KR: "069500"}
    env.run()
    context = env.a.contexts[-1]
    assert context.index_bars is not None
    assert context.index_bars.index[-1].date() == FRI  # 오늘 봉은 빠진다


def test_engine_without_index_gives_empty_context(env: Env):
    env.run()
    assert env.a.contexts[-1].index_bars is None


def test_index_error_is_a_warning_not_a_stop(env: Env):
    env.engine.index_symbols = {KR: "NOPE"}
    env.a.signals = {"005930": Side.BUY}
    report = env.run()
    assert report.status is RunStatus.OK
    assert any("시장 지수(NOPE)" in w for w in report.warnings)
    assert len(report.orders) == 1  # 시장 필터를 안 쓰는 전략은 그대로 주문


# ---- 장 마감 전 손절 확인 ----

MON_1317 = datetime(2026, 10, 26, 13, 17, tzinfo=KST)


def stop_check_env(**kw) -> Env:
    env = make_env(stop_loss_only=True, **kw)
    env.clock.now = MON_1317
    return env


def test_stop_loss_check_places_only_stop_loss_sells():
    env = stop_check_env(prices={"005930": 70_000, "000660": 200_000, "035420": 150_000})
    hold(env, "005930")
    hold(env, "000660", strategy="b", avg=220_000)
    env.a.signals = {"005930": Side.SELL, "035420": Side.BUY}
    env.a.stop_loss = {"005930"}
    env.b.signals = {"000660": Side.SELL}  # 손절이 아닌 일반 매도
    report = env.run()

    assert report.status is RunStatus.OK
    assert [(r.order.symbol, r.order.side) for r in report.orders] == [("005930", Side.SELL)]
    assert report.skipped == []
    # 보유 종목만 보고, 마지막 봉은 지금 가격으로 만든 오늘 봉이다
    assert [(symbol, day) for symbol, day, _ in env.a.seen] == [("005930", MON)]
    assert [(symbol, day) for symbol, day, _ in env.b.seen] == [("000660", MON)]
    assert env.notification.title == "[모의투자] 국내 · 완료 · 손절 확인"
    assert env.notification.urgent is False


def test_stop_loss_check_sells_on_todays_price_drop():
    env = make_env(prices={"005930": 80_000})
    hold(env, "005930", strategy="trend_ma", avg=80_000)
    env.broker.set_price(KR, "005930", 70_000)  # 장중 -12.5%

    def engine(**kw) -> Engine:
        return Engine(
            broker=env.broker,
            store=env.store,
            notifier=env.notifier,
            strategies=[TrendMA(short=2, long=3, entry_window=1)],
            universe=env.engine.universe,
            clock=env.clock,
            **kw,
        )

    # 아침 실행은 어제 종가(80,000)로 판단해서 손절하지 않는다
    assert engine().run(KR).orders == []

    env.clock.now = MON_1317
    report = engine(stop_loss_only=True).run(KR)
    [sig] = report.signals
    assert (sig.stop_loss, sig.price, sig.bar_date) == (True, 70_000, MON)
    [result] = report.orders
    o = result.order
    # 지금 가격보다 3% 낮은 지정가로 판다
    assert (o.side, o.qty, o.limit_price) == (Side.SELL, 5, 67_900)
    assert "손절가 72,000" in env.message


def test_stop_loss_check_with_nothing_to_do_sends_no_notification():
    env = stop_check_env()
    hold(env, "005930")
    report = env.run()
    assert report.status is RunStatus.OK
    assert env.notifier.messages == []
    assert env.store.runs == [report]  # 기록은 남는다(웹앱 알림함)
    assert "손절할 종목이 없습니다" in env.store.summaries[-1]


def test_stop_loss_check_after_close_is_quiet():
    env = stop_check_env()
    env.clock.now = datetime(2026, 10, 26, 15, 40, tzinfo=KST)  # GitHub 예약 실행이 늦은 경우
    assert env.run().status is RunStatus.SKIPPED
    assert env.notifier.messages == []


def test_stop_loss_check_still_notifies_errors_and_warnings():
    env = stop_check_env()
    env.broker.fail_on.add("get_balance")
    assert env.run().status is RunStatus.ERROR
    assert env.notification.urgent

    env = stop_check_env()
    hold(env, "005930")
    env.broker.fail_on.add("get_price")
    report = env.run()
    assert "시세 오류로 손절 확인을 건너뜁니다" in report.warnings[0]
    assert env.notification.urgent


def test_stop_loss_check_dry_run_always_reports():
    env = stop_check_env(dry_run=True)
    env.run()
    assert env.notification.title == "[모의투자] 국내 · 완료 · 손절 확인 · 시험 실행"
    assert "손절할 종목이 없습니다" in env.message


# ---- 예약 실행 백업(하루 한 번) ----


def test_backup_run_after_done_does_nothing():
    env = make_env(once_per_day=True)
    env.a.signals = {"005930": Side.BUY}
    assert env.run().status is RunStatus.OK
    assert env.store.cache == {"run_done_paper_KR_trade": "2026-10-26"}

    env.clock.now += timedelta(hours=1)  # 백업 예약 실행
    env.a.signals = {"000660": Side.BUY}
    report = env.run()
    assert report.status is RunStatus.SKIPPED
    assert "이미 실행했습니다" in report.message
    assert len(env.broker_orders()) == 1  # 새 주문 없음
    assert len(env.store.runs) == 1  # 기록도 알림도 없다
    assert len(env.notifier.messages) == 1


def test_backup_run_retries_after_error():
    env = make_env(once_per_day=True)
    env.broker.fail_on.add("get_balance")
    env.a.signals = {"005930": Side.BUY}
    assert env.run().status is RunStatus.ERROR
    assert env.store.cache == {}

    env.broker.fail_on.clear()
    env.clock.now += timedelta(hours=1)
    assert len(env.run().orders) == 1


def test_paused_run_counts_as_done():
    env = make_env(once_per_day=True)
    env.store.set_pause(True)
    assert env.run().status is RunStatus.PAUSED
    env.store.set_pause(False)
    env.a.signals = {"005930": Side.BUY}
    assert env.run().status is RunStatus.SKIPPED


def test_once_per_day_runs_again_next_day():
    env = make_env(once_per_day=True)
    env.run()
    env.next_day()  # 화요일
    env.broker.set_bars(KR, "005930", bars_from_closes([70_000] * 10, end=MON))
    env.broker.set_bars(KR, "000660", bars_from_closes([200_000] * 10, end=MON))
    assert env.run().status is RunStatus.OK
    assert len(env.store.runs) == 2


def test_stop_loss_check_is_counted_separately():
    env = make_env(once_per_day=True)
    env.run()
    check = Engine(
        broker=env.broker,
        store=env.store,
        notifier=env.notifier,
        strategies=[env.a, env.b],
        universe=env.engine.universe,
        clock=env.clock,
        stop_loss_only=True,
        once_per_day=True,
    )
    env.clock.now = MON_1317
    assert check.run(KR).status is RunStatus.OK
    assert env.store.cache["run_done_paper_KR_stoploss"] == "2026-10-26"
    assert check.run(KR).status is RunStatus.SKIPPED


def test_without_once_per_day_every_run_counts():
    env = make_env()  # 손으로 실행
    env.run()
    assert env.run().status is RunStatus.OK
    assert env.store.cache == {}


def test_done_marker_errors_do_not_stop_the_run():
    env = make_env(once_per_day=True)
    env.store.fail_on.update({"get_cache", "put_cache"})
    env.a.signals = {"005930": Side.BUY}
    report = env.run()
    assert report.status is RunStatus.OK
    assert len(report.orders) == 1
    assert report.warnings == []


def test_dry_run_does_not_mark_done():
    env = make_env(once_per_day=True, dry_run=True)
    env.run()
    assert env.run().status is RunStatus.OK
    assert env.store.cache == {}
