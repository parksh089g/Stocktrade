"""설정에 맞는 증권사, 저장소, 알림을 만든다."""

import logging
from pathlib import Path

import pandas as pd
from pydantic import SecretStr

from trading.brokers.base import BrokerAdapter
from trading.brokers.fake import make_demo_broker
from trading.brokers.kis import KisBroker
from trading.brokers.kis_client import EncryptedTokenCache, FileTokenCache, KisClient, TokenCache
from trading.config import Settings
from trading.core.clock import now_utc, trading_date
from trading.core.log import redact
from trading.core.models import Market
from trading.llm.base import LLMClient
from trading.llm.claude import ClaudeLLM
from trading.llm.dart import DartSource
from trading.llm.edgar import EdgarSource
from trading.llm.screen import BuyScreener, DisclosureSource
from trading.notify.base import ConsoleNotifier, MultiNotifier, Notifier
from trading.notify.webpush import WebPushNotifier
from trading.storage.base import Store
from trading.storage.memory import MemoryStore
from trading.storage.supabase import SupabaseStore
from trading.universe import BENCHMARKS, Universe

logger = logging.getLogger(__name__)

TOKEN_DIR = Path(".cache")


class SetupError(Exception):
    """실행에 필요한 설정이 빠졌다."""


def build_broker(
    settings: Settings, universe: Universe, market: Market, *, seed: int = 0
) -> BrokerAdapter:
    """엔진용 증권사. 비교 지수(BENCHMARKS) 일봉도 받을 수 있게 해 둔다."""
    if settings.broker == "fake":
        # 가짜 일봉은 어제까지 만든다(오늘 봉은 장 시작 전이라 없음)
        today = pd.Timestamp(trading_date(market, now_utc()))
        end = (today - pd.offsets.BDay(1)).date()
        return make_demo_broker(universe, end=end, seed=seed, extra=BENCHMARKS.values())
    broker = build_kis_broker(settings, universe)
    for inst in BENCHMARKS.values():
        broker.add_instrument(inst)  # 목록에 없는 SPY의 거래소(AMEX)를 알려 준다
    return broker


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


def build_llm(settings: Settings) -> LLMClient | None:
    """LLM 보조 기능용 클라이언트. 쓰지 않거나 설정이 모자라면 None.

    LLM은 덤이라 설정이 틀려도 매매 실행을 멈추지 않는다. 로그에 이유만 남긴다.
    """
    provider = settings.llm_provider.strip().lower()
    if provider in ("", "none"):
        return None
    if provider != "anthropic":
        logger.warning("LLM_PROVIDER=%s는 지원하지 않아 LLM 기능을 끕니다", provider)
        return None
    if not settings.llm_api_key:
        logger.warning("LLM_API_KEY가 없어 LLM 기능을 끕니다")
        return None
    return ClaudeLLM(settings.llm_api_key.get_secret_value(), settings.llm_model)


def build_screener(settings: Settings, llm: LLMClient | None) -> BuyScreener | None:
    """매수 후보 악재 판정(섀도 모드). LLM과 시장별 공시 출처가 있어야 한다.

    출처가 없는 시장의 매수 신호는 '판정 못 함'으로 기록된다.
    """
    if settings.llm_screen == "off":
        return None
    if llm is None:
        logger.warning("LLM 설정이 없어 악재 판정을 하지 않습니다")
        return None
    sources: dict[Market, DisclosureSource] = {}
    if settings.dart_api_key:
        sources[Market.KR] = DartSource(settings.dart_api_key.get_secret_value())
    if settings.sec_user_agent:
        sources[Market.US] = EdgarSource(settings.sec_user_agent)
    secrets = settings.secret_values()
    return BuyScreener(llm, sources, clean=lambda text: redact(text, secrets))


# 웹앱 설정 화면에서 넣을 수 있는 값(engine_settings 칸 → Settings 이름). 비밀값은 Vault에 있다.
STORE_SETTINGS = ("llm_provider", "llm_model", "llm_summary", "llm_screen", "sec_user_agent")
STORE_SECRETS = ("llm_api_key", "dart_api_key")


def with_store_config(settings: Settings, store: Store | None = None) -> Settings:
    """GitHub(환경변수, .env)에 없는 LLM 설정을 웹앱에서 넣은 값으로 채운다.

    GitHub에 값이 있으면 그것이 먼저다. 읽지 못하면 로그만 남기고 그대로 돌려준다
    (LLM 기능만 꺼질 뿐 매매는 멈추지 않는다).
    """
    if store is None:
        if not settings.supabase_url or not settings.supabase_service_key:
            return settings
        store = SupabaseStore(
            settings.supabase_url,
            settings.supabase_service_key.get_secret_value(),
            settings.trading_mode,
        )
    try:
        config = store.get_engine_config()
    except Exception as e:
        logger.warning("웹앱 엔진 설정을 읽지 못했습니다(LLM 기능에만 영향): %s", type(e).__name__)
        return settings
    given = settings.model_fields_set
    update: dict = {}
    for name in STORE_SETTINGS:
        value = config.settings.get(name)
        if name not in given and value not in (None, ""):
            update[name] = value
    for name in STORE_SECRETS:
        value = config.secrets.get(name, "").strip()
        if name not in given and value:
            update[name] = SecretStr(value)
    if not update:
        return settings
    logger.info("웹앱에서 넣은 설정을 씁니다: %s", ", ".join(sorted(update)))
    return settings.model_copy(update=update)
