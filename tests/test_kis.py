from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tests.fake_kis import APP_KEY, APP_SECRET, FakeKis, kr_bar_rows, us_bar_rows
from tests.helpers import Clock, StrategyA, StrategyB
from trading.brokers import kis_check
from trading.brokers.base import BrokerError, UnsupportedOrderError
from trading.brokers.kis import KisBroker, parse_account, parse_kr_order, parse_us_order
from trading.brokers.kis_client import FileTokenCache, KisClient
from trading.core.clock import KST
from trading.core.models import (
    Instrument,
    Market,
    Order,
    OrderStatus,
    OrderType,
    Side,
    validate_bars,
)
from trading.engine.report import RunStatus
from trading.engine.runner import Engine
from trading.notify.base import MemoryNotifier
from trading.risk.config import RiskConfig
from trading.storage.memory import MemoryStore

D = Decimal
KR, US = Market.KR, Market.US
MON_0840 = datetime(2026, 10, 26, 8, 40, tzinfo=KST)
UNIVERSE = {
    KR: [Instrument(market=KR, symbol="005930", name="삼성전자")],
    US: [
        Instrument(market=US, symbol="AAPL", exchange="NASDAQ"),
        Instrument(market=US, symbol="JPM", exchange="NYSE"),
    ],
}


@pytest.fixture
def make(tmp_path):
    def factory(fake: FakeKis | None = None, clock=None) -> tuple[KisBroker, FakeKis]:
        fake = fake or FakeKis()
        clock = clock or Clock(MON_0840)
        client = KisClient(
            app_key=APP_KEY,
            app_secret=APP_SECRET,
            paper=fake.paper,
            token_cache=FileTokenCache(tmp_path / "token.json"),
            transport=fake.transport,
            clock=clock,
            sleep=lambda _: None,
        )
        return KisBroker(client, "12345678-01", UNIVERSE, clock=clock), fake

    return factory


def order(market=KR, symbol="005930", side=Side.BUY, qty=17, limit="70700") -> Order:
    return Order(
        market=market,
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=OrderType.LIMIT if limit else OrderType.MARKET,
        limit_price=D(limit) if limit else None,
        strategy="a",
        client_order_id="x",
    )


def last_body(fake: FakeKis) -> dict:
    import json

    return json.loads(fake.requests[-1].content)


# ---- 기본 ----


def test_parse_account():
    assert parse_account("12345678-01") == ("12345678", "01")
    assert parse_account("1234567801") == ("12345678", "01")
    with pytest.raises(ValueError, match="12345678-01"):
        parse_account("1234-5678")


def test_unknown_exchange_in_universe_is_rejected(make):
    client = make()[0]._client
    bad = {US: [Instrument(market=US, symbol="X", exchange="TOKYO")]}
    with pytest.raises(ValueError, match="TOKYO"):
        KisBroker(client, "12345678-01", bad)


@pytest.mark.parametrize(
    ("market", "day", "open_"),
    [
        (KR, date(2026, 10, 26), True),
        (KR, date(2026, 10, 9), False),  # 한글날
        (KR, date(2026, 12, 31), False),  # 연말 휴장
        (KR, date(2026, 10, 24), False),  # 토요일
        (US, date(2026, 11, 26), False),  # 추수감사절
        (US, date(2026, 10, 9), True),
    ],
)
def test_trading_days(make, market, day, open_):
    assert make()[0].is_trading_day(market, day) is open_


def test_prices(make):
    broker, fake = make()
    assert broker.get_price(KR, "005930") == D(70000)
    assert broker.get_price(US, "AAPL") == D("180.5000")
    fake.us_prices["AAPL"] = {"last": "", "base": "179.0000"}  # 장 시작 전
    assert broker.get_price(US, "AAPL") == D("179.0000")
    assert fake.requests[-1].url.params["EXCD"] == "NAS"


# ---- 일봉 ----


def test_kr_daily_bars_are_sorted_and_paged(make):
    broker, fake = make()
    fake.kr_bars["005930"] = kr_bar_rows(date(2026, 3, 2), list(range(50_000, 50_000 + 160)))
    newest = fake.kr_bars["005930"][0]["stck_bsop_date"]

    bars = broker.get_daily_bars(KR, "005930", 99)
    validate_bars(bars)
    assert len(bars) == 99
    assert bars.index[-1].strftime("%Y%m%d") == newest
    assert bars["close"].iloc[-1] == 50_000 + 159
    assert bars["volume"].iloc[-1] == 123456

    more = broker.get_daily_bars(KR, "005930", 130)  # 100개가 넘으면 날짜를 옮겨 더 받는다
    assert len(more) == 130
    assert more.index.is_monotonic_increasing and not more.index.has_duplicates


def test_us_daily_bars(make):
    broker, fake = make()
    fake.us_bars["JPM"] = us_bar_rows(date(2026, 3, 2), [200.0 + i for i in range(150)])
    bars = broker.get_daily_bars(US, "JPM", 120)
    validate_bars(bars)
    assert len(bars) == 120
    assert bars["close"].iloc[-1] == pytest.approx(349.0)
    daily = [r for r in fake.requests if r.url.path.endswith("dailyprice")]
    assert daily[0].url.params["BYMD"] == ""  # 처음엔 오늘부터
    assert daily[0].url.params["EXCD"] == "NYS"
    assert daily[1].url.params["BYMD"] != ""


def test_missing_bars_is_error(make):
    broker, _ = make()
    with pytest.raises(BrokerError, match="일봉"):
        broker.get_daily_bars(KR, "005930", 99)


# ---- 잔고, 보유 ----


def test_kr_balance_and_holdings(make):
    broker, fake = make()
    fake.kr_holdings = [
        {"pdno": "005930", "hldg_qty": "10", "pchs_avg_pric": "65000.0000"},
        {"pdno": "000660", "hldg_qty": "0", "pchs_avg_pric": "0"},  # 오늘 전량 매도
        {"pdno": "035420", "hldg_qty": "3", "pchs_avg_pric": "190000"},
    ]
    fake.kr_buyable = "3500000"
    fake.kr_summary = {"tot_evlu_amt": "4800000"}
    balance = broker.get_balance(KR)
    assert (balance.cash, balance.total_equity) == (D(3_500_000), D(4_800_000))
    holdings = broker.get_holdings(KR)
    assert [(h.symbol, h.qty, h.avg_price) for h in holdings] == [
        ("005930", 10, D(65000)),
        ("035420", 3, D(190000)),
    ]


def test_us_holdings_paper_queries_each_exchange(make):
    broker, fake = make()
    fake.us_holdings = {
        "NASD": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "5", "pchs_avg_pric": "170.1",
                  "ovrs_excg_cd": "NASD", "ovrs_stck_evlu_amt": "902.50"}],
        "NYSE": [{"ovrs_pdno": "KO", "ovrs_cblc_qty": "10", "pchs_avg_pric": "60",
                  "ovrs_excg_cd": "NYSE", "ovrs_stck_evlu_amt": "650.00"}],
    }  # fmt: skip
    fake.us_prices["KO"] = {"last": "65.0", "base": "64.0"}
    holdings = broker.get_holdings(US)
    assert {(h.symbol, h.qty) for h in holdings} == {("AAPL", 5), ("KO", 10)}
    queried = [
        r.url.params["OVRS_EXCG_CD"] for r in fake.requests if "inquire-balance" in r.url.path
    ]
    assert queried == ["NASD", "NYSE", "AMEX"]
    # 목록에 없던 KO의 거래소를 잔고에서 알아낸다
    broker.get_price(US, "KO")
    assert fake.requests[-1].url.params["EXCD"] == "NYS"

    balance = broker.get_balance(US)
    assert balance.cash == D("10000.00")
    assert balance.total_equity == D("10000.00") + D("902.50") + D("650.00")


def test_us_holdings_live_uses_all_us_code(make):
    broker, fake = make(FakeKis(paper=False))
    broker.get_holdings(US)
    queried = [
        r.url.params["OVRS_EXCG_CD"] for r in fake.requests if "inquire-balance" in r.url.path
    ]
    assert queried == ["NASD"]


# ---- 주문 ----


def test_kr_limit_buy(make):
    broker, fake = make()
    result = broker.place_order(order())
    assert result.status is OrderStatus.SUBMITTED
    assert result.broker_order_id == "0000000001"
    assert fake.requests[-1].headers["tr_id"] == "VTTC0012U"
    body = last_body(fake)
    assert body | {"CANO": "", "ACNT_PRDT_CD": ""} == {
        "CANO": "",
        "ACNT_PRDT_CD": "",
        "PDNO": "005930",
        "ORD_DVSN": "00",
        "ORD_QTY": "17",
        "ORD_UNPR": "70700",
        "EXCG_ID_DVSN_CD": "KRX",
        "SLL_TYPE": "",
        "CNDT_PRIC": "",
    }
    assert (body["CANO"], body["ACNT_PRDT_CD"]) == ("12345678", "01")


def test_kr_sell_and_market_order(make):
    broker, fake = make()
    broker.place_order(order(side=Side.SELL))
    assert fake.requests[-1].headers["tr_id"] == "VTTC0011U"
    assert last_body(fake)["SLL_TYPE"] == "01"
    broker.place_order(order(limit=None))
    assert (last_body(fake)["ORD_DVSN"], last_body(fake)["ORD_UNPR"]) == ("01", "0")


def test_live_tr_ids(make):
    broker, fake = make(FakeKis(paper=False))
    broker.place_order(order())
    broker.place_order(order(US, "AAPL", Side.SELL, qty=1, limit="182.35"))
    assert fake.tr_ids()[-2:] == ["TTTC0012U", "TTTT1006U"]


def test_us_orders(make):
    broker, fake = make()
    broker.place_order(order(US, "JPM", Side.BUY, qty=2, limit="250.10"))
    assert fake.requests[-1].headers["tr_id"] == "VTTT1002U"
    body = last_body(fake)
    assert (body["OVRS_EXCG_CD"], body["PDNO"], body["OVRS_ORD_UNPR"]) == ("NYSE", "JPM", "250.10")
    assert (body["ORD_DVSN"], body["SLL_TYPE"]) == ("00", "")

    broker.place_order(order(US, "AAPL", Side.SELL, qty=1, limit="182.35"))
    assert fake.requests[-1].headers["tr_id"] == "VTTT1001U"  # 모의투자 미국 매도
    assert last_body(fake)["SLL_TYPE"] == "00"


def test_us_market_order_is_refused(make):
    broker, _ = make()
    with pytest.raises(UnsupportedOrderError):
        broker.place_order(order(US, "AAPL", limit=None))


def test_us_symbol_without_exchange_is_error(make):
    broker, _ = make()
    with pytest.raises(BrokerError, match="거래소"):
        broker.place_order(order(US, "TSLA", limit="200"))


def test_rejected_order(make):
    broker, fake = make()
    fake.reject_orders_with = "주문가능금액을 초과 했습니다"
    result = broker.place_order(order())
    assert result.status is OrderStatus.REJECTED
    assert result.broker_order_id is None
    assert "주문가능금액" in result.message


def test_kr_orders_today_with_fills_and_paging(make):
    broker, fake = make()
    for _ in range(3):
        broker.place_order(order())
    fake.kr_orders[1].update(tot_ccld_qty="5", avg_prvs="70500", rmn_qty="12")
    fake.kr_orders[2].update(tot_ccld_qty="17", avg_prvs="70400", rmn_qty="0")

    found = broker.get_orders(KR, date(2026, 10, 26))
    assert [(r.status, r.filled_qty, r.avg_fill_price) for r in found] == [
        (OrderStatus.SUBMITTED, 0, None),
        (OrderStatus.PARTIALLY_FILLED, 5, D(70500)),
        (OrderStatus.FILLED, 17, D(70400)),
    ]
    assert found[0].created_at == datetime(2026, 10, 25, 23, 40, tzinfo=UTC)
    assert broker.get_orders(KR, date(2026, 10, 27)) == []


def test_us_orders_today(make):
    broker, fake = make()
    placed = broker.place_order(order(US, "AAPL", qty=3, limit="182.35"))
    fake.us_orders[0].update(ft_ccld_qty="3", ft_ccld_unpr3="181.20000000", nccs_qty="0")
    [found] = broker.get_orders(US, date(2026, 10, 26))
    assert found.broker_order_id == placed.broker_order_id
    assert (found.status, found.filled_qty, found.avg_fill_price) == (
        OrderStatus.FILLED,
        3,
        D("181.20000000"),
    )
    assert found.order.limit_price == D("182.35")


# ---- 응답 해석 ----


def kr_row(**kw) -> dict:
    row = {
        "ord_dt": "20261026", "ord_tmd": "090102", "odno": "1", "orgn_odno": "", "pdno": "005930",
        "sll_buy_dvsn_cd": "02", "ord_qty": "10", "ord_unpr": "70000", "ord_dvsn_cd": "00",
        "tot_ccld_qty": "0", "avg_prvs": "0", "rjct_qty": "0", "cncl_yn": "", "cnc_cfrm_qty": "0",
    }  # fmt: skip
    return row | kw


@pytest.mark.parametrize(
    ("changes", "status"),
    [
        ({}, OrderStatus.SUBMITTED),
        ({"rjct_qty": "10"}, OrderStatus.REJECTED),
        ({"cncl_yn": "Y"}, OrderStatus.CANCELLED),
        ({"cnc_cfrm_qty": "10"}, OrderStatus.CANCELLED),
        ({"tot_ccld_qty": "4", "avg_prvs": "69900"}, OrderStatus.PARTIALLY_FILLED),
        ({"tot_ccld_qty": "10", "avg_prvs": "69900"}, OrderStatus.FILLED),
    ],
)
def test_parse_kr_order_status(changes, status):
    assert parse_kr_order(kr_row(**changes)).status is status


def test_parse_kr_order_fields():
    r = parse_kr_order(kr_row(sll_buy_dvsn_cd="01", ord_dvsn_cd="01", ord_unpr="0"))
    assert (r.order.side, r.order.order_type, r.order.limit_price) == (
        Side.SELL,
        OrderType.MARKET,
        None,
    )
    assert r.created_at == datetime(2026, 10, 26, 0, 1, 2, tzinfo=UTC)


@pytest.mark.parametrize(("orgn_odno", "is_new"), [("", True), ("0000000000", True), (None, True),
                                                   ("0000014181", False)])  # fmt: skip
def test_parse_kr_order_skips_only_amend_rows(orgn_odno, is_new):
    # 모의투자는 새 주문 줄에도 원주문번호를 0000000000으로 채워 보낸다 (2026-10-26 확인)
    r = parse_kr_order(kr_row(orgn_odno=orgn_odno))
    assert (r is not None) is is_new


def test_parse_us_order():
    row = {
        "ord_dt": "20261026", "ord_tmd": "094000", "dmst_ord_dt": "20261026",
        "thco_ord_tmd": "224000", "odno": "9", "orgn_odno": "", "pdno": "AAPL",
        "sll_buy_dvsn_cd": "02", "ft_ord_qty": "3", "ft_ord_unpr3": "182.35000000",
        "ft_ccld_qty": "0", "ft_ccld_unpr3": "0", "prcs_stat_name": "거부",
        "rjct_rson": "1", "rjct_rson_name": "증거금 부족",
    }  # fmt: skip
    r = parse_us_order(row)
    assert r.status is OrderStatus.REJECTED
    assert r.message == "증거금 부족"
    assert r.created_at == datetime(2026, 10, 26, 13, 40, tzinfo=UTC)  # 한국 22:40
    no_kst = parse_us_order(row | {"dmst_ord_dt": "", "thco_ord_tmd": ""})
    assert no_kst.created_at == datetime(2026, 10, 26, 13, 40, tzinfo=UTC)  # 뉴욕 09:40
    assert parse_us_order(row | {"orgn_odno": "0000000000"}) is not None  # 새 주문
    assert parse_us_order(row | {"orgn_odno": "0000000008"}) is None  # 정정/취소 주문 줄


def test_parse_us_order_pads_order_number_like_order_response():
    """미국 모의투자 주문 내역은 주문번호 앞의 0을 빼고, 새 주문의 원주문번호를 "0"으로 준다."""
    row = {
        "ord_dt": "20261026", "ord_tmd": "093622", "dmst_ord_dt": "20261026",
        "thco_ord_tmd": "223622", "odno": "36551", "orgn_odno": "0", "pdno": "AAPL",
        "sll_buy_dvsn_cd": "02", "ft_ord_qty": "1", "ft_ord_unpr3": "275.22000000",
        "ft_ccld_qty": "0", "ft_ccld_unpr3": "0", "prcs_stat_name": "", "rjct_rson": "",
    }  # fmt: skip
    r = parse_us_order(row)
    assert r.broker_order_id == "0000036551"
    assert r.order.client_order_id == "KIS-0000036551"
    assert r.status is OrderStatus.SUBMITTED


# ---- 엔진과 함께 ----


def engine_with_kis(make, *, dry_run=False):
    clock = Clock(MON_0840)
    broker, fake = make(clock=clock)
    fake.kr_bars["005930"] = kr_bar_rows(date(2026, 10, 9), [70_000] * 11)  # 10/9~10/23
    a, b = StrategyA(), StrategyB()
    store, notifier = MemoryStore(), MemoryNotifier()
    engine = Engine(
        broker=broker,
        store=store,
        notifier=notifier,
        strategies=[a, b],
        universe=UNIVERSE,
        risk=RiskConfig(strategy_weights={"a": D("0.5"), "b": D("0.5")}),
        clock=clock,
        dry_run=dry_run,
    )
    return engine, fake, store, a, clock


def test_engine_buys_through_kis_and_records_fill_next_day(make):
    engine, fake, store, a, clock = engine_with_kis(make)
    a.signals = {"005930": Side.BUY}
    report = engine.run(KR)
    assert report.status is RunStatus.OK
    [kis_order] = fake.kr_orders
    assert (kis_order["ord_qty"], kis_order["ord_unpr"]) == ("17", "70700")

    # 장중에 체결됐다고 치고 다음 날 실행
    kis_order.update(tot_ccld_qty="17", avg_prvs="70500", rmn_qty="0")
    a.signals = {}
    clock.now += timedelta(days=1)
    second = engine.run(KR)
    assert [(f.qty, f.price) for f in second.fills] == [(17, D(70500))]
    [pos] = store.get_positions(KR)
    assert (pos.strategy, pos.qty) == ("a", 17)


def test_engine_dry_run_places_nothing(make):
    engine, fake, _, a, _ = engine_with_kis(make, dry_run=True)
    a.signals = {"005930": Side.BUY}
    report = engine.run(KR)
    assert fake.kr_orders == []
    assert [(o.symbol, o.qty, o.limit_price) for o in report.planned] == [("005930", 17, 70700)]


def test_engine_us_buys_through_kis_and_records_fill_next_day(make):
    engine, fake, store, a, clock = engine_with_kis(make)
    clock.now = datetime(2026, 10, 26, 22, 40, tzinfo=KST)  # 뉴욕 09:40
    fake.us_bars["AAPL"] = us_bar_rows(date(2026, 10, 9), [180.0] * 11)  # 10/9~10/23
    a.signals = {"AAPL": Side.BUY}
    report = engine.run(US)
    assert report.status is RunStatus.OK
    [placed] = report.orders
    [kis_order] = fake.us_orders
    assert kis_order["odno"] == placed.broker_order_id.lstrip("0")  # 내역은 0을 빼고 온다

    # 장중에 체결됐다고 치고 다음 날 실행
    qty = kis_order["ft_ord_qty"]
    kis_order.update(ft_ccld_qty=qty, ft_ccld_unpr3="180.40000000", nccs_qty="0")
    a.signals = {}
    clock.now += timedelta(days=1)
    second = engine.run(US)
    assert not [w for w in second.warnings if "찾지 못했습니다" in w]
    assert [(f.qty, f.price) for f in second.fills] == [(int(qty), D("180.40"))]
    [pos] = store.get_positions(US)
    assert (pos.strategy, pos.qty) == ("a", int(qty))


def test_engine_us_before_open_is_skipped(make):
    engine, fake, _, a, clock = engine_with_kis(make)
    clock.now = datetime(2026, 10, 26, 22, 0, tzinfo=KST)  # 뉴욕 09:00, 개장 전
    a.signals = {"AAPL": Side.BUY}
    report = engine.run(US)
    assert report.status is RunStatus.SKIPPED
    assert "09:30" in report.message
    assert fake.us_orders == []


# ---- 연결 확인 도구 ----


@pytest.fixture
def check_env(make, tmp_path, monkeypatch):
    from pathlib import Path

    universe_path = Path("config/universe.toml").resolve()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UNIVERSE_PATH", str(universe_path))
    broker, fake = make()
    fake.kr_bars["005930"] = kr_bar_rows(date(2026, 3, 2), [70_000] * 150)
    fake.order_date = datetime.now(KST).strftime("%Y%m%d")  # 도구는 실제 오늘 날짜로 조회한다
    monkeypatch.setattr(kis_check, "build_kis_broker", lambda settings, universe: broker)
    return fake


def test_kis_check_reads_without_ordering(check_env, capsys):
    assert kis_check.main(["--market", "KR"]) == 0
    out = capsys.readouterr().out
    assert "주문 가능 10,000,000" in out
    assert "005930 99개" in out
    assert check_env.kr_orders == []


def test_kis_check_order_test_paper(check_env, capsys):
    assert kis_check.main(["--market", "KR", "--order-test", "KR"]) == 0
    out = capsys.readouterr().out
    assert "1주 지정가 56,000" in out  # 70,000 × 0.8
    assert "주문 내역: SUBMITTED" in out
    assert len(check_env.kr_orders) == 1


def test_kis_check_order_test_us(check_env, capsys):
    check_env.us_bars["AAPL"] = us_bar_rows(date(2026, 3, 2), [180.0] * 150)
    assert kis_check.main(["--market", "US", "--order-test", "US"]) == 0
    out = capsys.readouterr().out
    assert "1주 지정가 144.40" in out  # 180.50 × 0.8
    assert "주문 내역: SUBMITTED" in out
    assert len(check_env.us_orders) == 1


def test_kis_check_order_test_refused_in_live(check_env, monkeypatch, capsys):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("BROKER", "kis")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert kis_check.main(["--order-test", "KR"]) == 2
    assert check_env.kr_orders == []
