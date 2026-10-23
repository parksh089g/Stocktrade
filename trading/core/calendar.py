"""시장 달력: 개장일. 증권사와 상관없는 시장 정보라 core에 둔다.

holidays 패키지의 거래소 휴장일(XKRX, XNYS)을 쓴다. 갑자기 정해진 임시 휴장은 모를 수 있는데,
그날 낸 주문은 증권사가 거부하고 알림으로 드러난다.
"""

from datetime import date

import holidays

from trading.core.models import Market

_CALENDARS = {
    Market.KR: holidays.financial_holidays("XKRX"),
    Market.US: holidays.financial_holidays("XNYS"),
}


def is_trading_day(market: Market, day: date) -> bool:
    """그 시장 현지 날짜 기준 개장일인지."""
    return day.weekday() < 5 and day not in _CALENDARS[market]
