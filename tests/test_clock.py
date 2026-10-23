from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from trading.core.clock import ensure_utc, format_kst, to_kst, trading_date
from trading.core.models import Market


def test_ensure_utc_rejects_naive():
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 10, 23, 9, 0))


def test_ensure_utc_converts():
    kst_time = datetime(2026, 10, 23, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    assert ensure_utc(kst_time) == datetime(2026, 10, 23, 0, 0, tzinfo=UTC)


def test_kst_display():
    dt = datetime(2026, 10, 23, 6, 30, tzinfo=UTC)
    assert to_kst(dt).hour == 15
    assert format_kst(dt) == "2026-10-23 15:30 KST"


def test_trading_date_differs_by_market():
    # UTC 15:30 = 한국 다음 날 00:30, 뉴욕 같은 날 11:30
    dt = datetime(2026, 10, 23, 15, 30, tzinfo=UTC)
    assert trading_date(Market.KR, dt) == date(2026, 10, 24)
    assert trading_date(Market.US, dt) == date(2026, 10, 23)
