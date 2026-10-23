"""리스크 설정."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RiskConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # 전략별 자금 배분(시장별 총평가금액 대비). 합이 1 이하.
    strategy_weights: dict[str, Decimal] = Field(
        default_factory=lambda: {"trend_ma": Decimal("0.5"), "rsi_rebound": Decimal("0.5")}
    )
    # 한 전략이 동시에 들 종목 수. 한 번 매수 목표 금액 = 전략 배분 / slots
    slots_per_strategy: int = Field(default=4, ge=1)
    # 한 종목에 들어갈 수 있는 최대 비중(모든 전략 합산, 총평가금액 대비)
    max_symbol_weight: Decimal = Field(default=Decimal("0.2"), gt=0, le=1)
    # 지정가 = 기준가(전날 종가) × (1 ± 버퍼). 매도는 체결이 잘 되도록 더 넉넉히 둔다.
    buy_limit_buffer: Decimal = Field(default=Decimal("0.01"), ge=0, le=Decimal("0.1"))
    sell_limit_buffer: Decimal = Field(default=Decimal("0.03"), ge=0, le=Decimal("0.1"))
    # 수량 계산 때 수수료 등을 위해 남겨 두는 비율
    fee_margin: Decimal = Field(default=Decimal("0.003"), ge=0, le=Decimal("0.05"))

    @model_validator(mode="after")
    def _check_weights(self) -> "RiskConfig":
        if any(w <= 0 for w in self.strategy_weights.values()):
            raise ValueError("전략 배분은 0보다 커야 합니다")
        if sum(self.strategy_weights.values()) > 1:
            raise ValueError("전략 배분의 합이 1을 넘습니다")
        return self
