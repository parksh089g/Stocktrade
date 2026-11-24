import pytest

from tests.fake_postgrest import FakePostgrest
from trading.brokers.kis import KisBroker
from trading.brokers.kis_client import EncryptedTokenCache, FileTokenCache
from trading.config import Settings
from trading.core.models import Market
from trading.engine.factory import (
    SetupError,
    build_broker,
    build_llm,
    build_notifier,
    build_screener,
    build_store,
    build_token_cache,
)
from trading.llm.base import FakeLLM
from trading.notify.base import ConsoleNotifier, MultiNotifier
from trading.notify.webpush import WebPushNotifier
from trading.storage import check
from trading.storage.memory import MemoryStore
from trading.storage.supabase import SupabaseStore
from trading.universe import BENCHMARKS


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_fake_broker_uses_memory_and_console():
    s = settings(supabase_url="https://x.supabase.co", supabase_service_key="sb_secret_x")
    store = build_store(s)
    assert isinstance(store, MemoryStore)  # 가짜 주문이 실제 기록에 섞이지 않게
    assert isinstance(build_notifier(s, store), ConsoleNotifier)


def test_kis_requires_supabase():
    with pytest.raises(SetupError, match="SUPABASE"):
        build_store(settings(broker="kis"))


def test_kis_uses_supabase_and_web_push():
    s = settings(
        broker="kis",
        supabase_url="https://x.supabase.co",
        supabase_service_key="sb_secret_x",
        vapid_private_key="vapid",
        vapid_subject="mailto:me@example.com",
    )
    store = build_store(s)
    assert isinstance(store, SupabaseStore)
    assert store.mode == "paper"
    notifier = build_notifier(s, store)
    assert isinstance(notifier, MultiNotifier)
    assert [type(n) for n in notifier.notifiers] == [ConsoleNotifier, WebPushNotifier]


def test_kis_without_vapid_key_only_prints():
    s = settings(broker="kis", supabase_url="https://x.supabase.co", supabase_service_key="k")
    assert isinstance(build_notifier(s, build_store(s)), ConsoleNotifier)


# ---- 비교 지수(BENCHMARKS) ----

EMPTY_UNIVERSE = {Market.KR: [], Market.US: []}


@pytest.mark.parametrize("market", list(Market))
def test_demo_broker_has_benchmark_bars(market):
    broker = build_broker(settings(), EMPTY_UNIVERSE, market)
    inst = BENCHMARKS[market]
    assert not broker.get_daily_bars(market, inst.symbol, 5).empty


def test_kis_broker_knows_benchmark_exchange():
    s = settings(broker="kis", kis_app_key="k", kis_app_secret="s", kis_account_no="12345678-01")
    broker = build_broker(s, EMPTY_UNIVERSE, Market.US)
    assert isinstance(broker, KisBroker)
    # 목록에 없는 SPY도 일봉을 받을 수 있다(거래소를 모르면 BrokerError)
    assert broker._order_exchange("SPY") == "AMEX"


# ---- KIS 토큰 캐시 ----

SUPABASE = {"supabase_url": "https://x.supabase.co", "supabase_service_key": "sb_secret_x"}


def test_token_cache_is_a_file_on_this_pc():
    cache = build_token_cache(settings(broker="kis", kis_app_secret="s", **SUPABASE))
    assert isinstance(cache, FileTokenCache)
    assert cache.path.name == "kis_token_paper.json"


def test_token_cache_is_encrypted_in_supabase_on_github_actions():
    s = settings(broker="kis", kis_app_secret="s", github_actions=True, **SUPABASE)
    cache = build_token_cache(s)
    assert isinstance(cache, EncryptedTokenCache)
    assert cache.name == "kis_token_paper"
    assert isinstance(cache.store, SupabaseStore)


def test_token_cache_on_github_actions_without_supabase_is_a_file():
    s = settings(broker="kis", kis_app_secret="s", github_actions=True)
    assert isinstance(build_token_cache(s), FileTokenCache)


# ---- Supabase 연결 확인 도구 ----


@pytest.fixture
def fake(monkeypatch) -> FakePostgrest:
    fake = FakePostgrest()
    real = check.SupabaseStore
    monkeypatch.setattr(
        check,
        "SupabaseStore",
        lambda url, key, mode: real(url, key, mode, transport=fake.transport),
    )
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", fake.key)
    return fake


def test_storage_check_ok(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert check.main() == 0
    out = capsys.readouterr().out
    assert "주문 이유 칸(reason) 확인" in out
    assert "Supabase 연결 확인 완료" in out
    assert fake.tables["positions"] == []  # 확인용 행은 지운다


def test_storage_check_reports_missing_reason_column(fake, tmp_path, monkeypatch, capsys):
    # schema.sql을 다시 실행하기 전: 새 엔진이 주문을 기록하지 못하니 미리 알려 준다
    monkeypatch.chdir(tmp_path)
    fake.missing_columns["orders"] = {"reason"}
    assert check.main() == 1
    out = capsys.readouterr().out
    assert "reason" in out
    assert "schema.sql" in out


def test_storage_check_reports_failure(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fake.tables["trading_switch"] = []
    assert check.main() == 1
    assert "schema.sql" in capsys.readouterr().out


def test_storage_check_needs_settings(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert check.main() == 2


# ---- LLM 보조 기능 ----


def test_llm_is_off_by_default():
    s = settings()
    assert build_llm(s) is None
    assert build_screener(s, None) is None


def test_screen_needs_llm():
    with pytest.raises(SetupError, match="LLM_PROVIDER"):
        build_screener(settings(llm_screen="shadow"), None)


def test_screen_sources_follow_settings():
    llm = FakeLLM()
    only_kr = build_screener(settings(llm_screen="shadow", dart_api_key="dart-key-1"), llm)
    assert only_kr is not None
    assert set(only_kr.sources) == {Market.KR}
    both = build_screener(
        settings(llm_screen="shadow", dart_api_key="k" * 8, sec_user_agent="me a@b.c"), llm
    )
    assert both is not None
    assert set(both.sources) == {Market.KR, Market.US}


def test_llm_and_dart_keys_are_redacted():
    s = settings(llm_api_key="llm-secret-1", dart_api_key="dart-secret-1")
    assert {"llm-secret-1", "dart-secret-1"} <= set(s.secret_values())
