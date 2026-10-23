"""시간 유틸. 저장은 UTC, 화면과 알림은 한국 시간(KST)."""

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from trading.core.models import Market

KST = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")


def now_utc() -> datetime:
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """시간대가 있는 datetime을 UTC로 바꾼다. 시간대가 없으면 오류."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("시간대 정보가 없는 datetime은 쓸 수 없습니다")
    return dt.astimezone(UTC)


def to_kst(dt: datetime) -> datetime:
    return ensure_utc(dt).astimezone(KST)


def format_kst(dt: datetime) -> str:
    """알림/화면용 한국 시간 문자열. 예: 2026-10-23 15:30 KST"""
    return to_kst(dt).strftime("%Y-%m-%d %H:%M KST")


def trading_date(market: "Market", dt: datetime) -> date:
    """그 시장의 현지 날짜. 같은 날 중복 주문 판단에 쓴다."""
    return ensure_utc(dt).astimezone(market.timezone).date()
