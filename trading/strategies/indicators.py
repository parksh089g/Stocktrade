"""전략에서 쓰는 기술적 지표."""

import pandas as pd


def sma(close: pd.Series, window: int) -> pd.Series:
    """단순 이동평균. 데이터가 window개 모이기 전에는 NaN."""
    return close.rolling(window, min_periods=window).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder 방식 RSI(0~100).

    지수평활이라 앞부분 값은 덜 정확하다. 기간의 몇 배(예: 14일이면 100개 정도)를 넣어야
    마지막 값이 안정된다. 하락이 전혀 없으면 100, 변동이 전혀 없으면 50.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    out = 100 - 100 / (1 + avg_gain / avg_loss)
    return out.mask((avg_gain == 0) & (avg_loss == 0), 50.0)
