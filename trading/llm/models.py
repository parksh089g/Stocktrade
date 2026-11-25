"""LLM 보조 기능의 기록용 모델. 실행 기록(runs.report)에 그대로 들어간다."""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field

from trading.core.models import Market


class Disclosure(BaseModel):
    """공시 한 건(국내 DART, 미국 SEC EDGAR)."""

    title: str
    filed: date  # 접수일(그 나라 날짜)
    source: str  # DART | SEC
    url: str = ""


class Verdict(StrEnum):
    VETO = "VETO"  # 악재가 있어 매수를 거부했을 것
    OK = "OK"  # 악재 없음(또는 최근 공시 없음)
    UNKNOWN = "UNKNOWN"  # 공시나 LLM 오류로 판정하지 못함


class ScreenResult(BaseModel):
    """매수 신호 하나에 대한 악재 판정. 섀도 모드에서는 기록만 하고 주문에 쓰지 않는다."""

    market: Market
    symbol: str
    strategy: str
    verdict: Verdict
    reason: str
    mode: str = "shadow"
    model: str = ""
    disclosures: list[Disclosure] = Field(default_factory=list)
    since: date | None = None  # 이 날짜부터의 공시를 봤다
