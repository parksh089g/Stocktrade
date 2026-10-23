import pytest

_SETTINGS_ENV = (
    "TRADING_MODE",
    "BROKER",
    "GITHUB_ACTIONS",
    "KIS_APP_KEY",
    "KIS_APP_SECRET",
    "KIS_ACCOUNT_NO",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "UNIVERSE_PATH",
    "MAX_DAILY_BUY_KRW",
    "MAX_DAILY_BUY_USD",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """테스트가 로컬 환경변수(.env 포함)에 영향받지 않게 한다."""
    for name in _SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)
