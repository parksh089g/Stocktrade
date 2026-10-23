import pytest

from tests.fake_postgrest import FakePostgrest
from trading.config import Settings
from trading.engine.factory import SetupError, build_notifier, build_store
from trading.notify.base import ConsoleNotifier, MultiNotifier
from trading.notify.webpush import WebPushNotifier
from trading.storage import check
from trading.storage.memory import MemoryStore
from trading.storage.supabase import SupabaseStore


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
    assert "Supabase 연결 확인 완료" in out
    assert fake.tables["positions"] == []  # 확인용 행은 지운다


def test_storage_check_reports_failure(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fake.tables["trading_switch"] = []
    assert check.main() == 1
    assert "schema.sql" in capsys.readouterr().out


def test_storage_check_needs_settings(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert check.main() == 2
