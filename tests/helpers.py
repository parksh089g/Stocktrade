from datetime import date, datetime

import pandas as pd

from trading.core.models import Market, Position, Side, Signal
from trading.strategies.base import Strategy


def bars_from_closes(
    closes: list[float], start: str = "2026-01-05", end: date | None = None
) -> pd.DataFrame:
    """종가만으로 만든 일봉(시가=고가=저가=종가). end를 주면 그날로 끝난다."""
    close = pd.Series(closes, dtype=float)
    index = (
        pd.bdate_range(end=end, periods=len(closes))
        if end
        else pd.bdate_range(start, periods=len(closes))
    )
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1000},
    ).set_index(index)


class Clock:
    """테스트에서 시간을 옮길 수 있는 시계."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Scripted(Strategy):
    """종목별로 정해 둔 신호를 낸다. 무엇을 받았는지 기록한다."""

    id = "scripted"

    def __init__(self, lookback: int = 5) -> None:
        self._lookback = lookback
        self.signals: dict[str, Side] = {}
        self.stop_loss: set[str] = set()  # 여기 있는 종목의 매도 신호는 손절로 낸다
        self.seen: list[tuple[str, date, Position | None]] = []

    @property
    def lookback(self) -> int:
        return self._lookback

    def _evaluate(self, market: Market, symbol, bars, position) -> Signal | None:
        self.seen.append((symbol, bars.index[-1].date(), position))
        side = self.signals.get(symbol)
        if side is None:
            return None
        stop = side is Side.SELL and symbol in self.stop_loss
        return self._signal(market, symbol, side, bars, f"{self.id} 신호", stop_loss=stop)


class StrategyA(Scripted):
    id = "a"


class StrategyB(Scripted):
    id = "b"
