"""환경변수/.env에서 읽는 설정.

기본 모드는 모의투자(paper)다. live는 사람이 직접 TRADING_MODE=live로 설정할 때만 켜지고,
실전 키는 이 PC에 두지 않으므로 GitHub Actions 안에서만 허용한다.
"""

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TradingMode = Literal["paper", "live"]
BrokerName = Literal["fake", "kis"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    trading_mode: TradingMode = "paper"
    broker: BrokerName = "fake"

    kis_app_key: SecretStr | None = None
    kis_app_secret: SecretStr | None = None
    kis_account_no: SecretStr | None = None

    supabase_url: str | None = None
    supabase_service_key: SecretStr | None = None

    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    # GitHub Actions가 자동으로 GITHUB_ACTIONS=true를 넣어 준다
    github_actions: bool = Field(default=False)

    @model_validator(mode="after")
    def _guard_live(self) -> "Settings":
        if self.trading_mode == "live":
            if self.broker == "fake":
                raise ValueError("live 모드에서는 fake 증권사를 쓸 수 없습니다")
            if not self.github_actions:
                raise ValueError("live 모드는 GitHub Actions에서만 실행할 수 있습니다")
        return self

    @property
    def is_live(self) -> bool:
        return self.trading_mode == "live"

    def secret_values(self) -> list[str]:
        """로그에서 가려야 할 비밀값 목록."""
        secrets = [
            self.kis_app_key,
            self.kis_app_secret,
            self.kis_account_no,
            self.supabase_service_key,
            self.telegram_bot_token,
        ]
        return [s.get_secret_value() for s in secrets if s and s.get_secret_value()]
