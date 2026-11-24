"""환경변수/.env에서 읽는 설정.

기본 모드는 모의투자(paper)다. live는 사람이 직접 TRADING_MODE=live로 설정할 때만 켜지고,
실전 키는 이 PC에 두지 않으므로 GitHub Actions 안에서만 허용한다.
"""

from decimal import Decimal
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TradingMode = Literal["paper", "live"]
BrokerName = Literal["fake", "kis"]
# LLM 회사. none이면 LLM 기능을 쓰지 않는다. 어댑터를 붙일 때 여기에 이름을 더한다.
LlmProvider = Literal["none"]
# 매수 후보 악재 판정: off(안 함), shadow(판정을 기록만 하고 주문에는 반영하지 않음)
LlmScreenMode = Literal["off", "shadow"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    trading_mode: TradingMode = "paper"
    broker: BrokerName = "fake"

    kis_app_key: SecretStr | None = None
    kis_app_secret: SecretStr | None = None
    kis_account_no: SecretStr | None = None

    supabase_url: str | None = None
    supabase_service_key: SecretStr | None = None

    # 웹 푸시 VAPID 개인키(python -m trading.notify.vapid로 만든다)와 연락처(mailto: 또는 https:)
    vapid_private_key: SecretStr | None = None
    vapid_subject: str = "mailto:stocktrade@example.com"

    universe_path: str = "config/universe.toml"
    # 하루 매수 금액 상한(시장별). 비우면 상한 없음. 실전 전환 초기에 작게 둔다.
    max_daily_buy_krw: Decimal | None = Field(default=None, gt=0)
    max_daily_buy_usd: Decimal | None = Field(default=None, gt=0)

    # LLM 보조 기능(trading/llm). 주문과 리스크 규칙에는 손대지 않는다.
    llm_provider: LlmProvider = "none"
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_summary: bool = True  # LLM이 있으면 실행 요약을 알림 맨 앞에 붙인다
    llm_screen: LlmScreenMode = "off"
    # 악재 판정용 공시 출처. 국내 OpenDART API 키, 미국 SEC EDGAR User-Agent("이름 연락처 이메일")
    dart_api_key: SecretStr | None = None
    sec_user_agent: str | None = None

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
            self.vapid_private_key,
            self.llm_api_key,
            self.dart_api_key,
        ]
        return [s.get_secret_value() for s in secrets if s and s.get_secret_value()]
