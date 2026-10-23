"""저장소 테스트. 같은 테스트를 메모리 저장소와 Supabase 저장소(가짜 서버)에 모두 돌린다."""

from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from tests.fake_postgrest import FakePostgrest
from trading.core.clock import KST
from trading.core.models import Market, Order, OrderResult, OrderStatus, OrderType, Position, Side
from trading.engine.report import RunReport, RunStatus
from trading.storage.base import PushSubscription, StoreError
from trading.storage.memory import MemoryStore
from trading.storage.supabase import SupabaseStore

D = Decimal
KR, US = Market.KR, Market.US
MON = date(2026, 10, 26)


def supabase(fake: FakePostgrest, mode: str = "paper", **kw) -> SupabaseStore:
    return SupabaseStore(
        "https://test.supabase.co",
        fake.key,
        mode,
        transport=fake.transport,
        sleep=lambda _: None,
        **kw,
    )


@pytest.fixture(params=["memory", "supabase"])
def store(request):
    if request.param == "memory":
        return MemoryStore()
    fake = FakePostgrest()
    store = supabase(fake)
    store.test_fake = fake  # 테스트에서 가짜 서버 표에 직접 넣을 때 쓴다
    return store


def add_subscription(store, endpoint: str) -> None:
    """웹앱이 구독을 등록하는 것을 흉내 낸다."""
    sub = PushSubscription(endpoint=endpoint, p256dh="p", auth="a")
    if isinstance(store, MemoryStore):
        store.add_push_subscription(sub)
    else:
        store.test_fake.tables["push_subscriptions"].append(sub.model_dump())


def position(symbol="005930", strategy="a", qty=10, avg="69600.333333333333333333", market=KR):
    return Position(
        market=market,
        symbol=symbol,
        strategy=strategy,
        qty=qty,
        avg_price=D(avg),
        opened_at=datetime(2026, 10, 26, 8, 40, tzinfo=KST),
    )


def order_result(status=OrderStatus.SUBMITTED, filled=0, avg=None, cid="20261026-KR-005930-BUY-a"):
    return OrderResult(
        order=Order(
            market=KR,
            symbol="005930",
            side=Side.BUY,
            qty=17,
            order_type=OrderType.LIMIT,
            limit_price=D(70_700),
            strategy="a",
            client_order_id=cid,
        ),
        broker_order_id="B-1",
        status=status,
        filled_qty=filled,
        avg_fill_price=avg,
        created_at=datetime(2026, 10, 26, 8, 40, tzinfo=KST),
    )


# ---- 공통 동작 ----


def test_pause_switch(store):
    assert not store.get_pause().paused
    store.set_pause(True, "점검")
    pause = store.get_pause()
    assert (pause.paused, pause.reason) == (True, "점검")
    store.set_pause(False)
    assert not store.get_pause().paused


def test_positions_roundtrip_keeps_exact_decimal(store):
    store.upsert_position(position())
    store.upsert_position(position(symbol="AAPL", market=US, avg="180.5"))
    [got] = store.get_positions(KR)
    assert got == position()  # 소수점 끝자리까지 그대로

    store.upsert_position(position(qty=4))
    assert store.get_positions(KR)[0].qty == 4
    assert len(store.get_positions(KR)) == 1

    store.delete_position(KR, "005930", "a")
    assert store.get_positions(KR) == []
    assert len(store.get_positions(US)) == 1


def test_orders_roundtrip_and_open_orders(store):
    store.save_order(order_result())
    [got] = store.get_orders(KR, MON)
    assert got == order_result()
    assert store.get_orders(KR, date(2026, 10, 27)) == []
    assert len(store.get_open_orders(KR)) == 1

    filled = order_result(OrderStatus.FILLED, filled=17, avg=D("70512.5"))
    store.save_order(filled)  # 같은 client_order_id는 덮어쓴다
    assert store.get_orders(KR, MON) == [filled]
    assert store.get_open_orders(KR) == []


def test_order_date_is_market_local_date(store):
    # 한국 10/27 00:30 = UTC 10/26 15:30 → 국내 주문은 10/27 주문
    late = order_result().model_copy(
        update={"created_at": datetime(2026, 10, 26, 15, 30, tzinfo=UTC)}
    )
    store.save_order(late)
    assert len(store.get_orders(KR, date(2026, 10, 27))) == 1
    assert store.get_orders(KR, MON) == []


def test_save_run_returns_distinct_ids(store):
    report = RunReport(market=KR, mode="paper", started_at=datetime(2026, 10, 26, tzinfo=UTC))
    first = store.save_run(report, "요약 1")
    second = store.save_run(report.model_copy(update={"status": RunStatus.ERROR}), "요약 2")
    assert first and second and first != second


def test_push_subscriptions(store):
    add_subscription(store, "https://push.example/1")
    add_subscription(store, "https://push.example/2")
    assert {s.endpoint for s in store.get_push_subscriptions()} == {
        "https://push.example/1",
        "https://push.example/2",
    }
    store.delete_push_subscription("https://push.example/1")
    assert [s.endpoint for s in store.get_push_subscriptions()] == ["https://push.example/2"]


# ---- Supabase 전용 ----


def test_paper_and_live_records_are_separate():
    fake = FakePostgrest()
    paper, live = supabase(fake, "paper"), supabase(fake, "live")
    paper.upsert_position(position())
    paper.save_order(order_result())
    assert live.get_positions(KR) == []
    assert live.get_orders(KR, MON) == []
    assert len(paper.get_positions(KR)) == 1


def test_secret_key_only_in_apikey_header():
    fake = FakePostgrest()
    supabase(fake).get_pause()
    request = fake.requests[-1]
    assert request.headers["apikey"] == "sb_secret_test"
    assert "authorization" not in request.headers


def test_legacy_jwt_key_also_in_authorization_header():
    fake = FakePostgrest(key="eyJhbGciOiJIUzI1NiJ9.legacy")
    supabase(fake).get_pause()
    assert fake.requests[-1].headers["authorization"] == "Bearer eyJhbGciOiJIUzI1NiJ9.legacy"


def test_retries_server_and_connection_errors():
    fake = FakePostgrest()
    fake.fail_next = [503, httpx.ConnectError("down")]
    assert not supabase(fake).get_pause().paused
    assert len(fake.requests) == 3


def test_gives_up_after_retries():
    fake = FakePostgrest()
    fake.fail_next = [503, 503, 503]
    with pytest.raises(StoreError, match="503"):
        supabase(fake).get_pause()


def test_client_errors_are_not_retried_and_hide_key():
    fake = FakePostgrest()
    fake.fail_next = [409]
    with pytest.raises(StoreError) as err:
        supabase(fake).save_order(order_result())
    assert len(fake.requests) == 1
    assert "sb_secret_test" not in str(err.value)


def test_run_insert_is_not_retried():
    fake = FakePostgrest()
    fake.fail_next = [503]
    with pytest.raises(StoreError):
        supabase(fake).save_run(
            RunReport(market=KR, mode="paper", started_at=datetime(2026, 10, 26, tzinfo=UTC)), "x"
        )
    assert fake.tables["runs"] == []
    assert len(fake.requests) == 1


def test_missing_switch_row_is_an_error():
    fake = FakePostgrest()
    fake.tables["trading_switch"] = []
    with pytest.raises(StoreError, match=r"schema\.sql"):
        supabase(fake).get_pause()


def test_run_record_has_summary_and_report_json():
    fake = FakePostgrest()
    report = RunReport(market=KR, mode="paper", started_at=datetime(2026, 10, 26, tzinfo=UTC))
    run_id = supabase(fake).save_run(report, "[모의투자] 국내 · 완료")
    [row] = fake.tables["runs"]
    assert run_id == str(row["id"])
    assert row["summary"] == "[모의투자] 국내 · 완료"
    assert row["report"]["market"] == "KR"
