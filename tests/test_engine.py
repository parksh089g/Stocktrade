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
from trading.llm.base import FakeLLM, LLMError
from trading.llm.models import Disclosure, Verdict
from trading.llm.screen import BuyScreener, DisclosureSource
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
    # 신호 근거는 체결 동기화로 주문 기록이 바뀌어도 남는다
    assert [o["reason"] for o in fake.tables["orders"]] == ["a 신호", "a 신호"]
    assert len(fake.tables["runs"]) == 3
    assert env.notification.url == "/runs/3"
    # 웹앱의 비중/수익 곡선용 기록
    assert fake.tables["runs"][0]["report"]["balance"]["cash"] == "10000000"


# ---- 웹앱용 기록: 잔고, 가격, 비교 지수 ----

BENCH = {KR: Instrument(market=KR, symbol="069500", name="KODEX 200")}


def test_run_records_balance_and_prices(env: Env):
    hold(env, "005930")  # 5주, 평단 80,000(현재가는 마지막 종가 70,000)
    report = env.run()
    assert report.balance is not None
    assert (report.balance.cash, report.balance.total_equity) == (D(10_000_000), D(10_350_000))
    # 전략이 본 종목의 마지막 확정 종가
    assert report.prices == {"005930": D(70_000), "000660": D(200_000)}
    assert report.benchmark is None  # 비교 지수를 정하지 않았다


def test_run_records_benchmark_close_without_todays_bar():
    env = make_env(benchmarks=BENCH)
    env.broker.set_bars(KR, "069500", bars_from_closes([30_000, 31_000, 32_000], end=MON))
    report = env.run()
    b = report.benchmark
    assert (b.symbol, b.name, b.close, b.bar_date) == ("069500", "KODEX 200", D(31_000), FRI)


@pytest.mark.parametrize("bars", [None, "only today"])
def test_benchmark_problems_are_only_logged(bars):
    env = make_env(benchmarks=BENCH)
    if bars:
        env.broker.set_bars(KR, "069500", bars_from_closes([30_000], end=MON))
    env.a.signals = {"005930": Side.BUY}
    report = env.run()
    assert report.status is RunStatus.OK
    assert report.benchmark is None
    # 경고로 올리면 알림이 '긴급'이 되고 손절 확인 알림도 매번 간다
    assert report.warnings == []
    assert not env.notification.urgent
    assert len(report.orders) == 1


def test_stop_loss_check_records_current_prices_but_no_benchmark():
    env = stop_check_env(benchmarks=BENCH)
    env.broker.set_bars(KR, "069500", bars_from_closes([30_000] * 3, end=MON))
    hold(env, "005930")
    env.broker.set_price(KR, "005930", 75_000)
    report = env.run()
    assert report.prices == {"005930": D(75_000)}  # 지금 가격
    assert report.benchmark is None  # 하루 한 점은 매매 실행에서만 남긴다
    assert report.balance is not None


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


@pytest.mark.parametrize(
    ("now", "day_over"),
    [
        (datetime(2026, 10, 26, 15, 30, tzinfo=KST), True),  # 장 마감 후
        (datetime(2026, 10, 26, 7, 50, tzinfo=KST), False),  # 주문 가능 시간 전: 나중에 다시 돈다
    ],
)
def test_after_close_counts_as_done_but_before_open_does_not(now, day_over):
    env = make_env(once_per_day=True)
    env.clock.now = now
    assert env.run().status is RunStatus.SKIPPED
    assert ("run_done_paper_KR_trade" in env.store.cache) is day_over
    assert len(env.store.runs) == 1


def test_holiday_counts_as_done():
    env = make_env(once_per_day=True)
    env.broker.holidays.add(MON)
    assert "휴장일" in env.run().message
    env.clock.now += timedelta(hours=1)  # 다른 곳에서 부른 예약 실행
    assert "이미 실행했습니다" in env.run().message
    assert len(env.store.runs) == 1  # 휴장일 기록과 알림은 한 번만
    assert len(env.notifier.messages) == 1


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


# ---- LLM 보조 기능(요약, 악재 판정 섀도 모드) ----


class OneSource(DisclosureSource):
    def __init__(self, title: str = "주요사항보고서(유상증자결정)", error=None):
        self.title = title
        self.error = error
        self.asked: list[str] = []

    def recent(self, instrument, since, until):
        self.asked.append(instrument.symbol)
        if self.error:
            raise self.error
        return [Disclosure(title=self.title, filed=FRI, source="DART")]


VETO = '{"verdict": "VETO", "reason": "유상증자 결정 공시"}'
SUMMARY = (
    "삼성전자 17주 매수 주문을 냈습니다.\n공시 점검에서는 유상증자가 걸렸지만 기록만 했습니다."
)


def llm_env(*, summary=SUMMARY, screen=VETO, source=None, **kw):
    """요약용 LLM과 판정용 LLM을 따로 둬서 어느 쪽이 불렸는지 본다."""
    summary_llm, screen_llm = FakeLLM(summary), FakeLLM(screen)
    source = source or OneSource()
    env = make_env(llm=summary_llm, screener=BuyScreener(screen_llm, {KR: source}), **kw)
    return env, summary_llm, screen_llm, source


def test_shadow_veto_is_recorded_but_order_still_goes_out():
    env, _, screen_llm, source = llm_env()
    env.a.signals = {"005930": Side.BUY}
    report = env.run()

    # 섀도 모드: 판정이 VETO여도 주문은 그대로 나간다
    [result] = report.orders
    assert (result.order.symbol, result.order.side) == ("005930", Side.BUY)
    assert len(env.broker_orders()) == 1
    [screen] = report.screens
    assert (screen.symbol, screen.strategy, screen.verdict) == ("005930", "a", Verdict.VETO)
    assert source.asked == ["005930"] and len(screen_llm.calls) == 1
    assert "악재 판정(기록만, 주문에 반영 안 함): 거부했을 것 1" in env.message
    assert " · 삼성전자(005930): 유상증자 결정 공시" in env.message
    assert report.warnings == [] and not env.notification.urgent
    # 실행 기록(runs.report)에도 남는다
    assert env.store.runs[-1].screens == [screen]


def test_summary_goes_first_in_notification_and_record():
    env, summary_llm, _, _ = llm_env()
    env.a.signals = {"005930": Side.BUY}
    report = env.run()

    assert report.ai_summary == SUMMARY
    assert env.message.split("\n")[1] == f"요약(AI): {SUMMARY.splitlines()[0]}"
    assert f"요약(AI): {SUMMARY}" in env.store.summaries[-1]
    # 요약 입력은 결과 문장이고, 그 안에 악재 판정도 들어 있다
    [call] = summary_llm.calls
    assert "매수 삼성전자(005930) 17주" in call.prompt and "악재 판정" in call.prompt


def test_llm_failures_never_block_orders_or_make_alerts_urgent():
    source = OneSource(error=RuntimeError("공시 서버 오류"))
    env, _, _, _ = llm_env(summary=LLMError("timeout"), source=source)
    env.a.signals = {"005930": Side.BUY}
    report = env.run()

    assert report.status is RunStatus.OK
    assert len(report.orders) == 1
    assert report.screens[0].verdict is Verdict.UNKNOWN
    assert report.ai_summary is None
    assert "요약(AI)" not in env.message
    assert report.warnings == [] and not env.notification.urgent


def test_screener_crash_is_contained():
    class Broken(BuyScreener):
        def screen(self, *a, **kw):
            raise RuntimeError("bug")

    env = make_env(screener=Broken(FakeLLM(), {}))
    env.a.signals = {"005930": Side.BUY}
    report = env.run()
    assert report.status is RunStatus.OK and len(report.orders) == 1
    assert report.screens == []


def test_no_llm_calls_on_quiet_days_sells_or_pause():
    env, summary_llm, screen_llm, _ = llm_env()
    env.run()  # 신호 없음
    hold(env, "005930")
    env.a.signals = {"005930": Side.SELL}
    env.next_day()
    report = env.run()  # 매도만: 판정 안 함, 요약은 함
    assert report.screens == [] and screen_llm.calls == []
    assert len(summary_llm.calls) == 1

    env.store.set_pause(True, "휴가")
    env.a.signals = {"000660": Side.BUY}
    env.next_day()
    env.run()  # 일시정지 중 매수 신호는 주문 대상이 아니라 판정하지 않는다
    assert screen_llm.calls == []


def test_stop_loss_check_does_not_screen():
    env, _, screen_llm, source = llm_env(stop_loss_only=True)
    env.clock.now = MON_1317
    env.a.signals = {"005930": Side.BUY}
    env.run()
    assert source.asked == [] and screen_llm.calls == []
