from datetime import UTC, datetime

import httpx
import pytest

from tests.fake_kis import APP_KEY, APP_SECRET, FakeKis
from trading.brokers.base import BrokerError
from trading.brokers.kis_client import (
    PAPER_URL,
    CachedToken,
    FileTokenCache,
    KisApiError,
    KisClient,
)

NOW = datetime(2026, 10, 25, 23, 40, tzinfo=UTC)  # 한국 10/26 08:40
PRICE = "/uapi/domestic-stock/v1/quotations/inquire-price"
PRICE_PARAMS = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def timer(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make(fake: FakeKis, tmp_path, *, time: FakeTime | None = None, **kw) -> KisClient:
    time = time or FakeTime()
    return KisClient(
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        paper=fake.paper,
        token_cache=FileTokenCache(tmp_path / "token.json"),
        transport=fake.transport,
        clock=lambda: NOW,
        sleep=time.sleep,
        timer=time.timer,
        **kw,
    )


def price(client: KisClient):
    return client.get(PRICE, "FHKST01010100", PRICE_PARAMS)


# ---- 토큰 ----


def test_token_is_issued_once_and_cached_to_file(tmp_path):
    fake = FakeKis()
    client = make(fake, tmp_path)
    price(client)
    price(client)
    assert fake.tokens_issued == 1

    # 다음 실행(새 클라이언트)도 파일의 토큰을 다시 쓴다
    price(make(fake, tmp_path))
    assert fake.tokens_issued == 1

    cache_file = tmp_path / "token.json"
    assert cache_file.stat().st_mode & 0o777 == 0o600
    assert APP_KEY not in cache_file.read_text()  # 앱키는 저장하지 않는다


def test_expired_cached_token_is_replaced(tmp_path):
    fake = FakeKis()
    client = make(fake, tmp_path)
    FileTokenCache(tmp_path / "token.json").save(
        client._key_id, CachedToken("old", datetime(2026, 10, 25, 23, 45, tzinfo=UTC))
    )  # 5분 남음 → 새로 받는다
    price(client)
    assert fake.tokens_issued == 1


def test_token_for_other_app_key_is_not_used(tmp_path):
    fake = FakeKis()
    FileTokenCache(tmp_path / "token.json").save(
        "someone-else", CachedToken("other", datetime(2026, 10, 27, tzinfo=UTC))
    )
    price(make(fake, tmp_path))
    assert fake.tokens_issued == 1


def test_token_expiry_from_kis_is_kst(tmp_path):
    fake = FakeKis()
    client = make(fake, tmp_path)
    price(client)
    # "2026-10-27 08:00:00"(한국) = 10/26 23:00 UTC
    assert client._token.expires_at == datetime(2026, 10, 26, 23, 0, tzinfo=UTC)


def test_rejected_token_is_renewed_once(tmp_path):
    fake = FakeKis()
    client = make(fake, tmp_path)
    price(client)
    fake.reject_token = True
    fake.token = "fake-token-0002"
    assert price(client).body["output"]["stck_prpr"] == "70000"
    assert fake.tokens_issued == 2


def test_token_issue_failure(tmp_path):
    fake = FakeKis()
    fake.inject = [
        httpx.Response(403, json={"error_code": "EGW00133", "error_description": "1분당 1회"})
    ]
    with pytest.raises(BrokerError, match=r"토큰 발급 실패.*EGW00133"):
        price(make(fake, tmp_path))


# ---- 헤더, 오류, 재시도 ----


def test_request_headers(tmp_path):
    fake = FakeKis()
    price(make(fake, tmp_path))
    request = fake.requests[-1]
    assert str(request.url).startswith(PAPER_URL)
    assert request.headers["tr_id"] == "FHKST01010100"
    assert request.headers["authorization"] == "Bearer fake-token-0001"


def test_business_error_raises_kis_api_error(tmp_path):
    fake = FakeKis()
    client = make(fake, tmp_path)
    price(client)
    fake.inject = [{"rt_cd": "1", "msg_cd": "APBK0919", "msg1": "주문가능금액을 초과했습니다"}]
    with pytest.raises(KisApiError) as err:
        price(client)
    assert err.value.code == "APBK0919"
    assert APP_KEY not in str(err.value) and APP_SECRET not in str(err.value)


def test_rate_limit_is_retried(tmp_path):
    fake = FakeKis()
    time = FakeTime()
    client = make(fake, tmp_path, time=time)
    price(client)
    fake.inject = [
        {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}
    ] * 2
    assert price(client).body["rt_cd"] == "0"
    assert 1.0 in time.sleeps and 2.0 in time.sleeps


def test_get_retries_connection_errors_but_post_does_not(tmp_path):
    fake = FakeKis()
    client = make(fake, tmp_path)
    price(client)
    fake.inject = [httpx.ConnectError("down")]
    assert price(client).body["rt_cd"] == "0"

    fake.inject = [httpx.ReadTimeout("slow")]
    before = len(fake.requests)
    with pytest.raises(BrokerError, match="연결 실패"):
        client.post("/uapi/domestic-stock/v1/trading/order-cash", "VTTC0012U", {})
    assert len(fake.requests) == before + 1  # 주문은 다시 보내지 않는다


def test_server_error_without_body_is_broker_error(tmp_path):
    fake = FakeKis()
    client = make(fake, tmp_path)
    price(client)
    fake.inject = [502, 502, 502]
    with pytest.raises(BrokerError, match="502"):
        price(client)


# ---- 호출 간격 ----


def test_paper_calls_are_spaced(tmp_path):
    fake = FakeKis()
    time = FakeTime()
    client = make(fake, tmp_path, time=time)
    for _ in range(3):
        price(client)
    # 토큰 발급 + 조회 3번: 0.5초 간격
    assert time.sleeps == [0.5, 0.5, 0.5]


def test_overseas_calls_are_one_per_second(tmp_path):
    fake = FakeKis()
    time = FakeTime()
    client = make(fake, tmp_path, time=time)
    params = {"AUTH": "", "EXCD": "NAS", "SYMB": "AAPL"}
    for _ in range(3):
        client.get(
            "/uapi/overseas-price/v1/quotations/price", "HHDFS00000300", params, overseas=True
        )
    assert time.sleeps[-2:] == [1.0, 1.0]


def test_live_uses_shorter_interval(tmp_path):
    fake = FakeKis(paper=False)
    time = FakeTime()
    client = make(fake, tmp_path, time=time)
    price(client)
    price(client)
    assert all(s <= 0.06 for s in time.sleeps)
    assert not str(fake.requests[-1].url).startswith(PAPER_URL)


# ---- 연속 조회 ----


def test_get_pages_follows_continuation(tmp_path):
    fake = FakeKis()
    fake.kr_holdings = [
        {"pdno": f"00000{i}", "hldg_qty": "1", "pchs_avg_pric": "1"} for i in range(5)
    ]
    client = make(fake, tmp_path)
    rows, first = client.get_pages(
        "/uapi/domestic-stock/v1/trading/inquire-balance",
        "VTTC8434R",
        {"INQR_DVSN": "02", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""},
        list_key="output1",
        ctx_keys=("CTX_AREA_FK100", "CTX_AREA_NK100"),
    )
    assert [r["pdno"] for r in rows] == [f"00000{i}" for i in range(5)]
    assert first["output2"][0]["tot_evlu_amt"] == "10000000"
    continued = [r for r in fake.requests if r.headers.get("tr_cont") == "N"]
    assert len(continued) == 2
