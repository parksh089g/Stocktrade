import numpy as np
import pandas as pd
import pytest

from trading.strategies.indicators import rsi, sma


def test_sma():
    out = sma(pd.Series([1.0, 2.0, 3.0, 4.0]), 2)
    assert out.isna().iloc[0]
    assert out.iloc[1:].tolist() == [1.5, 2.5, 3.5]


def test_rsi_warmup_is_nan():
    out = rsi(pd.Series(np.arange(30, dtype=float)), 14)
    assert out.iloc[:14].isna().all()
    assert out.iloc[14:].notna().all()


@pytest.mark.parametrize(
    ("closes", "expected"),
    [
        (np.arange(1, 40, dtype=float), 100.0),  # 계속 오르면 100
        (np.arange(40, 1, -1, dtype=float), 0.0),  # 계속 내리면 0
        (np.full(40, 10.0), 50.0),  # 변동이 없으면 50
    ],
)
def test_rsi_extremes(closes, expected):
    assert rsi(pd.Series(closes), 14).iloc[-1] == pytest.approx(expected)


def _wilder_textbook(closes: list[float], period: int) -> float:
    """교과서식 Wilder RSI: 첫 평균은 단순평균, 이후 (이전*(n-1)+현재)/n."""
    deltas = np.diff(closes)
    gains, losses = np.clip(deltas, 0, None), np.clip(-deltas, 0, None)
    avg_g, avg_l = gains[:period].mean(), losses[:period].mean()
    for g, lo in zip(gains[period:], losses[period:], strict=True):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + lo) / period
    return 100 - 100 / (1 + avg_g / avg_l)


def test_rsi_matches_textbook_after_warmup():
    rng = np.random.default_rng(42)
    closes = list(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 98))))
    ours = rsi(pd.Series(closes), 14).iloc[-1]
    assert ours == pytest.approx(_wilder_textbook(closes, 14), abs=0.1)
