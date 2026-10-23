"""호가 단위. 지정가 주문 가격은 호가 단위에 맞아야 한다."""

from decimal import ROUND_FLOOR, Decimal

from trading.core.models import Market

# 국내 주식 호가 단위(2023년 개편, 코스피/코스닥 공통): (이 가격 미만, 단위)
# ETF/ETN은 단위가 다르다(5원). 2,000원 이상에서는 아래 단위가 모두 5의 배수라 그대로 써도 맞는다.
_KR_TICKS = (
    (Decimal(2_000), Decimal(1)),
    (Decimal(5_000), Decimal(5)),
    (Decimal(20_000), Decimal(10)),
    (Decimal(50_000), Decimal(50)),
    (Decimal(200_000), Decimal(100)),
    (Decimal(500_000), Decimal(500)),
)
_KR_TOP_TICK = Decimal(1_000)
_US_TICK = Decimal("0.01")


def tick_size(market: Market, price: Decimal) -> Decimal:
    if market is Market.US:
        return _US_TICK
    for upper, tick in _KR_TICKS:
        if price < upper:
            return tick
    return _KR_TOP_TICK


def floor_to_tick(market: Market, price: Decimal) -> Decimal:
    """호가 단위로 내림."""
    tick = tick_size(market, price)
    return (price / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
