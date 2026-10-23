"""① 추세 추종: 단기/장기 이동평균 교차.

- 매수: 오늘 포함 최근 entry_window일 안에 단기선이 장기선을 위로 뚫었고(골든크로스),
  지금도 위에 있을 때.
  하루 실행을 놓쳐도 며칠 안이면 따라 들어가고, 오래된 추세에 뒤늦게 올라타지는 않는다.
- 매도: 단기선이 장기선 아래에 있을 때. 교차한 날을 놓쳐도 다음 실행에서 판다.
"""

import pandas as pd

from trading.core.models import Market, Position, Side, Signal
from trading.strategies.base import Strategy
from trading.strategies.indicators import sma


class TrendMA(Strategy):
    id = "trend_ma"

    def __init__(self, short: int = 20, long: int = 60, entry_window: int = 3) -> None:
        if not 0 < short < long:
            raise ValueError("이동평균 기간은 0 < short < long 이어야 합니다")
        if entry_window < 1:
            raise ValueError("entry_window는 1 이상이어야 합니다")
        self.short = short
        self.long = long
        self.entry_window = entry_window

    @property
    def lookback(self) -> int:
        # 장기선이 entry_window+1일 동안 계산돼 있어야 교차 여부를 볼 수 있다
        return self.long + self.entry_window

    def _evaluate(
        self, market: Market, symbol: str, bars: pd.DataFrame, position: Position | None
    ) -> Signal | None:
        close = bars["close"]
        short_ma = sma(close, self.short)
        long_ma = sma(close, self.long)
        above = short_ma > long_ma
        s, lo = short_ma.iloc[-1], long_ma.iloc[-1]
        ma_text = (
            f"{self.short}일선 {market.format_price(s)}, {self.long}일선 {market.format_price(lo)}"
        )

        if position is None:
            # 마지막 날 위에 있고 그 전 entry_window일 중 아래였던 날이 있으면, 그 사이에 교차했다
            crossed_recently = not above.iloc[-(self.entry_window + 1) : -1].all()
            if above.iloc[-1] and crossed_recently:
                return self._signal(market, symbol, Side.BUY, bars, f"골든크로스: {ma_text}")
            return None

        if s < lo:
            return self._signal(market, symbol, Side.SELL, bars, f"데드크로스: {ma_text}")
        return None
