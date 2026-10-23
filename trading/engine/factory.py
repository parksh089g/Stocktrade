"""설정에 맞는 증권사, 저장소, 알림을 만든다."""

from pathlib import Path

import pandas as pd

from trading.brokers.base import BrokerAdapter
from trading.brokers.fake import make_demo_broker
from trading.brokers.kis import KisBroker
from trading.brokers.kis_client import EncryptedTokenCache, FileTokenCache, KisClient, TokenCache
from trading.config import Settings
from trading.core.clock import now_utc, trading_date
from trading.core.models import Market
from trading.notify.base import ConsoleNotifier, MultiNotifier, Notifier
from trading.notify.webpush import WebPushNotifier
from trading.storage.base import Store
from trading.storage.memory import MemoryStore
from trading.storage.supabase import SupabaseStore
from trading.universe import Universe

TOKEN_DIR = Path(".cache")


class SetupError(Exception):
    """실행에 필요한 설정이 빠졌다."""


def build_broker(
    settings: Settings, universe: Universe, market: Market, *, seed: int = 0
) -> BrokerAdapter:
    if settings.broker == "fake":
        # 가짜 일봉은 어제까지 만든다(오늘 봉은 장 시작 전이라 없음)
        today = pd.Timestamp(trading_date(market, now_utc()))
        return make_demo_broker(universe, end=(today - pd.offsets.BDay(1)).date(), seed=seed)
    return build_kis_broker(settings, universe)


def build_kis_broker(settings: Settings, universe: Universe) -> KisBroker:
    required = {
        "KIS_APP_KEY": settings.kis_app_key,
        "KIS_APP_SECRET": settings.kis_app_secret,
        "KIS_ACCOUNT_NO": settings.kis_account_no,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SetupError(f"{', '.join(missing)} 설정이 필요합니다")
    assert settings.kis_app_key and settings.kis_app_secret and settings.kis_account_no
    client = KisClient(
        app_key=settings.kis_app_key.get_secret_value(),
        app_secret=settings.kis_app_secret.get_secret_value(),
        paper=not settings.is_live,
        token_cache=build_token_cache(settings),
    )
    try:
        return KisBroker(client, settings.kis_account_no.get_secret_value(), universe)
    except ValueError as e:
        raise SetupError(str(e)) from e


def build_token_cache(settings: Settings) -> TokenCache:
    """KIS 토큰 캐시. 로컬은 .cache/ 파일을 쓴다.

    GitHub Actions는 실행마다 새 컴퓨터라 Supabase에 암호화해 둔다.
    Supabase 설정이 없으면 Actions에서도 파일을 쓴다(그 실행 안에서만 다시 쓴다).
    """
    name = f"kis_token_{settings.trading_mode}"
    secret = settings.kis_app_secret
    if (
        settings.github_actions
        and settings.supabase_url
        and settings.supabase_service_key
        and secret
    ):
        store = SupabaseStore(
            settings.supabase_url,
            settings.supabase_service_key.get_secret_value(),
            settings.trading_mode,
        )
        return EncryptedTokenCache(store, name, secret.get_secret_value())
    return FileTokenCache(TOKEN_DIR / f"{name}.json")


def build_store(settings: Settings, *, dry_run: bool = False) -> Store:
    """fake 증권사와 시험 실행은 메모리 저장소를 쓴다(실제 기록에 섞이지 않게)."""
    if settings.broker == "fake" or dry_run:
        return MemoryStore()
    if not settings.supabase_url or not settings.supabase_service_key:
        raise SetupError("SUPABASE_URL, SUPABASE_SERVICE_KEY 설정이 필요합니다")
    return SupabaseStore(
        settings.supabase_url,
        settings.supabase_service_key.get_secret_value(),
        settings.trading_mode,
    )


def build_notifier(settings: Settings, store: Store) -> Notifier:
    """화면(Actions 로그)에는 항상 찍고, 실제 기록을 쓸 때 VAPID 키가 있으면 웹 푸시도 보낸다."""
    console = ConsoleNotifier()
    if isinstance(store, MemoryStore) or not settings.vapid_private_key:
        return console
    push = WebPushNotifier(
        store, settings.vapid_private_key.get_secret_value(), settings.vapid_subject
    )
    return MultiNotifier([console, push])
