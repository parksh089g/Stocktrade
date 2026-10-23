"""설정에 맞는 저장소와 알림을 만든다."""

from trading.config import Settings
from trading.notify.base import ConsoleNotifier, MultiNotifier, Notifier
from trading.notify.webpush import WebPushNotifier
from trading.storage.base import Store
from trading.storage.memory import MemoryStore
from trading.storage.supabase import SupabaseStore


class SetupError(Exception):
    """실행에 필요한 설정이 빠졌다."""


def build_store(settings: Settings) -> Store:
    """fake 증권사는 메모리 저장소를 쓴다(가짜 주문이 실제 기록에 섞이지 않게)."""
    if settings.broker == "fake":
        return MemoryStore()
    if not settings.supabase_url or not settings.supabase_service_key:
        raise SetupError("SUPABASE_URL, SUPABASE_SERVICE_KEY 설정이 필요합니다")
    return SupabaseStore(
        settings.supabase_url,
        settings.supabase_service_key.get_secret_value(),
        settings.trading_mode,
    )


def build_notifier(settings: Settings, store: Store) -> Notifier:
    """화면(Actions 로그)에는 항상 찍고, VAPID 키가 있으면 웹 푸시도 보낸다."""
    console = ConsoleNotifier()
    if settings.broker == "fake" or not settings.vapid_private_key:
        return console
    push = WebPushNotifier(
        store, settings.vapid_private_key.get_secret_value(), settings.vapid_subject
    )
    return MultiNotifier([console, push])
