from decimal import Decimal

import pytest

from trading.core.models import Market
from trading.core.ticks import floor_to_tick, tick_size

D = Decimal


@pytest.mark.parametrize(
    ("price", "tick"),
    [
        (1_999, 1),
        (2_000, 5),
        (4_999, 5),
        (5_000, 10),
        (19_990, 10),
        (20_000, 50),
        (49_950, 50),
        (50_000, 100),
        (199_900, 100),
        (200_000, 500),
        (499_500, 500),
        (500_000, 1_000),
        (1_200_000, 1_000),
    ],
)
def test_kr_tick_size(price, tick):
    assert tick_size(Market.KR, D(price)) == D(tick)


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        ("70712.3", "70700"),
        ("199999", "199900"),
        ("4999", "4995"),
        ("1234.9", "1234"),
        ("512345", "512000"),
    ],
)
def test_kr_floor_to_tick(price, expected):
    assert floor_to_tick(Market.KR, D(price)) == D(expected)


def test_us_floor_to_cent():
    assert floor_to_tick(Market.US, D("180.567")) == D("180.56")
    assert floor_to_tick(Market.US, D("180.5")) == D("180.50")
