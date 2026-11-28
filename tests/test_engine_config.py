"""웹앱 설정 화면에서 넣은 LLM 설정과 키(Supabase engine_settings + Vault)를 엔진이 쓰는지."""

import httpx

from tests.fake_postgrest import FakePostgrest
from trading.config import Settings
from trading.engine.factory import build_llm, build_screener, with_store_config
from trading.llm.claude import ClaudeLLM
from trading.storage.base import EngineConfig
from trading.storage.memory import MemoryStore
from trading.storage.supabase import SupabaseStore


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def web_config() -> EngineConfig:
    return EngineConfig(
        settings={
            "llm_provider": "anthropic",
            "llm_model": "",
            "llm_summary": True,
            "llm_screen": "shadow",
            "sec_user_agent": "stocktrade me@example.com",
        },
        secrets={"llm_api_key": "sk-ant-web-1234", "dart_api_key": "dart-web-5678"},
    )


def test_web_values_fill_missing_settings():
    store = MemoryStore()
    store.engine_config = web_config()
    s = with_store_config(settings(), store)

    assert (s.llm_provider, s.llm_screen, s.sec_user_agent) == (
        "anthropic",
        "shadow",
        "stocktrade me@example.com",
    )
    assert s.llm_model is None  # 빈 값은 쓰지 않는다(어댑터 기본 모델)
    assert s.llm_api_key is not None and s.llm_api_key.get_secret_value() == "sk-ant-web-1234"
    # 웹앱에서 넣은 키도 로그 가림 목록에 들어간다
    assert {"sk-ant-web-1234", "dart-web-5678"} <= set(s.secret_values())
    llm = build_llm(s)
    assert isinstance(llm, ClaudeLLM)
    screener = build_screener(s, llm)
    assert screener is not None and len(screener.sources) == 2


def test_github_values_win():
    store = MemoryStore()
    store.engine_config = web_config()
    s = with_store_config(
        settings(llm_provider="none", llm_api_key="sk-ant-github", llm_screen="off"), store
    )
    assert s.llm_provider == "none" and s.llm_screen == "off"
    assert s.llm_api_key is not None and s.llm_api_key.get_secret_value() == "sk-ant-github"
    assert s.dart_api_key is not None  # GitHub에 없는 값은 웹앱 값으로 채운다


def test_read_failure_keeps_settings():
    store = MemoryStore()
    store.fail_on.add("get_engine_config")
    s = settings()
    assert with_store_config(s, store) is s


def test_no_supabase_means_nothing_to_read():
    s = settings()
    assert with_store_config(s) is s


def test_supabase_reads_settings_row_and_vault():
    pg = FakePostgrest()
    pg.tables["engine_settings"] = [
        {
            "id": 1,
            "llm_provider": "anthropic",
            "llm_model": "claude-haiku-5-5",
            "llm_summary": False,
            "llm_screen": "off",
            "sec_user_agent": "",
            "updated_at": "2026-11-25T00:00:00+00:00",
        }
    ]
    pg.vault = {"llm_api_key": "sk-ant-vault"}
    store = SupabaseStore(
        "https://x.supabase.co", pg.key, "paper", transport=httpx.MockTransport(pg.handle)
    )
    config = store.get_engine_config()
    assert config.settings == {
        "llm_provider": "anthropic",
        "llm_model": "claude-haiku-5-5",
        "llm_summary": False,
        "llm_screen": "off",
        "sec_user_agent": "",
    }
    assert config.secrets == {"llm_api_key": "sk-ant-vault"}
    rpc = [r for r in pg.requests if r.url.path.endswith("/rpc/get_engine_secrets")]
    assert [r.method for r in rpc] == ["POST"]

    s = with_store_config(settings(), store)
    assert (s.llm_model, s.llm_summary) == ("claude-haiku-5-5", False)


# ---- 웹앱에서 만든 웹 푸시 키 ----


def push_store(private_key: str) -> SupabaseStore:
    pg = FakePostgrest()
    pg.vault = {"vapid_private_key": private_key}
    return SupabaseStore(
        "https://x.supabase.co", pg.key, "paper", transport=httpx.MockTransport(pg.handle)
    )


def test_web_push_key_turns_on_web_push():
    from trading.engine.factory import build_notifier
    from trading.notify.base import ConsoleNotifier
    from trading.notify.vapid import generate_private_key
    from trading.notify.webpush import WebPushNotifier

    key = generate_private_key()
    store = push_store(key)
    s = with_store_config(settings(), store)
    assert s.vapid_private_key is not None and s.vapid_private_key.get_secret_value() == key
    assert key in s.secret_values()  # 로그에서 가린다
    notifier = build_notifier(s, store)
    assert [type(n) for n in notifier.notifiers] == [ConsoleNotifier, WebPushNotifier]


def test_github_push_key_wins_and_warns_when_different(caplog):
    from trading.notify.vapid import generate_private_key

    github, web = generate_private_key(), generate_private_key()
    s = with_store_config(settings(vapid_private_key=github), push_store(web))
    assert s.vapid_private_key is not None and s.vapid_private_key.get_secret_value() == github
    assert "VAPID_PRIVATE_KEY를 지우세요" in caplog.text
    assert github not in caplog.text and web not in caplog.text

    caplog.clear()
    with_store_config(settings(vapid_private_key=web), push_store(web))
    assert "다르게" not in caplog.text
