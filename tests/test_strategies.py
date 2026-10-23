from datetime import datetime, time
from decimal import Decimal

import pytest

from tests.helpers import bars_from_closes
from trading.core.models import Market, Position, Side
from trading.strategies import RsiRebound, TrendMA, create_strategy
from trading.strategies.indicators import rsi

KR = Market.KR


def position(strategy: str, avg_price, opened_on=None, bars=None) -> Position:
    day = opened_on or bars.index[0].date()
    return Position(
        market=KR,
        symbol="005930",
        strategy=strategy,
        qty=10,
        avg_price=Decimal(str(avg_price)),
        opened_at=datetime.combine(day, time(10), tzinfo=KR.timezone),
    )


# ---- ① 추세 추종 (3일선/5일선, 진입 허용 2일) ----

# 7번째(인덱스 7)에서 3일선이 5일선을 위로 뚫는다
GOLDEN = [20, 19, 18, 17, 16, 15, 14, 25, 26, 27]


@pytest.fixture
def trend() -> TrendMA:
    return TrendMA(short=3, long=5, entry_window=2)


def test_trend_buys_on_golden_cross(trend: TrendMA):
    bars = bars_from_closes(GOLDEN[:8])
    signal = trend.evaluate(KR, "005930", bars, None)

    assert signal is not None
    assert signal.side is Side.BUY
    assert signal.strategy == "trend_ma"
    assert signal.price == Decimal(25)
    assert signal.bar_date == bars.index[-1].date()
    assert "골든크로스" in signal.reason


def test_trend_buys_if_cross_was_within_entry_window(trend: TrendMA):
    signal = trend.evaluate(KR, "005930", bars_from_closes(GOLDEN[:9]), None)
    assert signal is not None and signal.side is Side.BUY


def test_trend_skips_old_cross(trend: TrendMA):
    # 교차 후 이틀이 지났으므로 뒤늦게 들어가지 않는다
    assert trend.evaluate(KR, "005930", bars_from_closes(GOLDEN), None) is None


def test_trend_holds_while_above(trend: TrendMA):
    bars = bars_from_closes(GOLDEN)
    assert trend.evaluate(KR, "005930", bars, position("trend_ma", 25, bars=bars)) is None


def test_trend_sells_when_short_below_long(trend: TrendMA):
    bars = bars_from_closes([10, 11, 12, 13, 14, 15, 16, 17, 9])
    # 평균단가 9.5 → 손절가 8.55, 종가 9는 손절선 위
    signal = trend.evaluate(KR, "005930", bars, position("trend_ma", 9.5, bars=bars))
    assert signal is not None
    assert signal.side is Side.SELL
    assert "데드크로스" in signal.reason
    assert not signal.stop_loss


def test_trend_stop_loss_even_while_uptrend(trend: TrendMA):
    bars = bars_from_closes(GOLDEN)  # 단기선이 장기선 위(추세 유지 중)
    # 평균단가 31 → 손절가 27.9, 종가 27
    signal = trend.evaluate(KR, "005930", bars, position("trend_ma", 31, bars=bars))
    assert signal is not None
    assert signal.side is Side.SELL
    assert "손절" in signal.reason
    assert signal.stop_loss
    # 평균단가 29.9 → 손절가 26.91, 종가 27은 손절선 위라 그대로 보유
    assert trend.evaluate(KR, "005930", bars, position("trend_ma", 29.9, bars=bars)) is None


def test_trend_no_signal_with_too_few_bars(trend: TrendMA):
    assert trend.lookback == 7
    assert trend.evaluate(KR, "005930", bars_from_closes(GOLDEN[1:7]), None) is None


def test_trend_rejects_bad_params():
    with pytest.raises(ValueError):
        TrendMA(short=60, long=20)
    with pytest.raises(ValueError):
        TrendMA(entry_window=0)
    with pytest.raises(ValueError):
        TrendMA(stop_loss=0)


def test_default_lookbacks_fit_one_kis_request():
    assert TrendMA().lookback <= 100
    assert RsiRebound().lookback <= 100


def test_strategy_rejects_other_strategys_position(trend: TrendMA):
    bars = bars_from_closes(GOLDEN)
    with pytest.raises(ValueError, match="rsi_rebound"):
        trend.evaluate(KR, "005930", bars, position("rsi_rebound", 25, bars=bars))


# ---- ② 단기 반등 (RSI 14, 30 탈출 매수, 50 회복/5% 손절/10일 보유 매도) ----

# 잔잔하게 오르내리다 12일 연속 하락 → RSI 12.5
BASE = [100.0 + (i % 2) for i in range(85)]
DROP = [BASE[-1] - 2 * (k + 1) for k in range(12)]


def rebound(x: float) -> list[float]:
    return BASE + DROP + [DROP[-1] + x]


@pytest.fixture
def rsi_strategy() -> RsiRebound:
    return RsiRebound()


def test_rsi_buys_when_leaving_oversold(rsi_strategy: RsiRebound):
    bars = bars_from_closes(rebound(6))
    r = rsi(bars["close"].tail(rsi_strategy.lookback), 14)
    assert r.iloc[-2] < 30 <= r.iloc[-1]  # 시나리오 전제 확인

    signal = rsi_strategy.evaluate(KR, "005930", bars, None)
    assert signal is not None
    assert signal.side is Side.BUY
    assert "과매도 탈출" in signal.reason


def test_rsi_no_buy_while_still_oversold(rsi_strategy: RsiRebound):
    bars = bars_from_closes(rebound(5))
    assert rsi(bars["close"].tail(rsi_strategy.lookback), 14).iloc[-1] < 30
    assert rsi_strategy.evaluate(KR, "005930", bars, None) is None


def test_rsi_stop_loss_first(rsi_strategy: RsiRebound):
    bars = bars_from_closes(rebound(5))  # 마지막 종가 82
    pos = position("rsi_rebound", 90, opened_on=bars.index[-2].date())  # 손절가 85.5
    signal = rsi_strategy.evaluate(KR, "005930", bars, pos)
    assert signal is not None
    assert signal.side is Side.SELL
    assert "손절" in signal.reason
    assert signal.stop_loss


def test_rsi_sells_on_recovery(rsi_strategy: RsiRebound):
    bars = bars_from_closes([*BASE, *range(100, 126, 2)])
    assert len(bars) >= rsi_strategy.lookback
    assert rsi(bars["close"].tail(rsi_strategy.lookback), 14).iloc[-1] >= 50
    pos = position("rsi_rebound", 110, opened_on=bars.index[-2].date())
    signal = rsi_strategy.evaluate(KR, "005930", bars, pos)
    assert signal is not None
    assert signal.side is Side.SELL
    assert "RSI 회복" in signal.reason
    assert not signal.stop_loss


def test_rsi_time_exit(rsi_strategy: RsiRebound):
    bars = bars_from_closes(rebound(5))  # RSI 50 미만, 마지막 종가 82
    held_10 = position("rsi_rebound", 82, opened_on=bars.index[-10].date())
    held_9 = position("rsi_rebound", 82, opened_on=bars.index[-9].date())

    signal = rsi_strategy.evaluate(KR, "005930", bars, held_10)
    assert signal is not None
    assert signal.side is Side.SELL
    assert "10일" in signal.reason
    assert rsi_strategy.evaluate(KR, "005930", bars, held_9) is None


def test_rsi_result_does_not_depend_on_extra_history(rsi_strategy: RsiRebound):
    short = bars_from_closes(rebound(6))
    long = bars_from_closes([100.0] * 200 + rebound(6))
    a = rsi_strategy.evaluate(KR, "005930", short, None)
    b = rsi_strategy.evaluate(KR, "005930", long, None)
    assert a is not None and b is not None
    assert a.reason == b.reason


def test_rsi_rejects_bad_params():
    with pytest.raises(ValueError):
        RsiRebound(oversold=60, exit_level=50)
    with pytest.raises(ValueError):
        RsiRebound(stop_loss=5)


def test_create_strategy():
    assert isinstance(create_strategy("trend_ma"), TrendMA)
    with pytest.raises(ValueError, match="알 수 없는 전략"):
        create_strategy("nope")
