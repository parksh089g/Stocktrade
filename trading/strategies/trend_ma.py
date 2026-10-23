"""① 추세 추종: 단기/장기 이동평균 교차.

- 매수: 오늘 포함 최근 entry_window일 안에 단기선이 장기선을 위로 뚫었고(골든크로스),
  지금도 위에 있을 때.
  하루 실행을 놓쳐도 며칠 안이면 따라 들어가고, 오래된 추세에 뒤늦게 올라타지는 않는다.
  market_filter를 주면 시장 지수가 그 기간 이동평균 아래일 때는 새로 사지 않는다.
- 매도(먼저 걸리는 것):
  1) 종가가 평균단가 대비 stop_loss 이상 하락(손절)
  2) trailing_stop을 주면, 산 뒤 가장 높았던 종가 대비 그만큼 하락(고점 대비 손절)
  3) 단기선이 장기선 아래(교차한 날을 놓쳐도 다음 실행에서 판다)
  1), 2)는 손실을 막는 매도라 일시정지 중에도 나간다.
"""

from decimal import Decimal

import pandas as pd

from trading.core.clock import trading_date
from trading.core.models import Market, Position, Side, Signal
from trading.strategies.base import MarketContext, Strategy
from trading.strategies.indicators import sma


class TrendMA(Strategy):
    id = "trend_ma"

    def __init__(
        self,
        short: int = 20,
        long: int = 60,
        entry_window: int = 3,
        stop_loss: float = 0.10,
        trailing_stop: float | None = None,
        market_filter: int | None = None,
    ) -> None:
        if not 0 < short < long:
            raise ValueError("이동평균 기간은 0 < short < long 이어야 합니다")
        if entry_window < 1:
            raise ValueError("entry_window는 1 이상이어야 합니다")
        if not 0 < stop_loss < 1:
            raise ValueError("stop_loss는 0과 1 사이 비율이어야 합니다")
        if trailing_stop is not None and not 0 < trailing_stop < 1:
            raise ValueError("trailing_stop은 0과 1 사이 비율이어야 합니다")
        if market_filter is not None and not 2 <= market_filter <= 99:
            raise ValueError("market_filter는 2~99일이어야 합니다(지수 일봉 99개 안)")
        self.short = short
        self.long = long
        self.entry_window = entry_window
        self.stop_loss = Decimal(str(stop_loss))
        self.trailing_stop = Decimal(str(trailing_stop)) if trailing_stop else None
        self.market_filter = market_filter

    @property
    def lookback(self) -> int:
        # 장기선이 entry_window+1일 동안 계산돼 있어야 교차 여부를 볼 수 있다
        return self.long + self.entry_window

    def _evaluate(
        self,
        market: Market,
        symbol: str,
        bars: pd.DataFrame,
        position: Position | None,
        context: MarketContext,
    ) -> Signal | None:
        close = bars["close"]
        short_ma = sma(close, self.short)
        long_ma = sma(close, self.long)
        above = short_ma > long_ma
        s, lo = short_ma.iloc[-1], long_ma.iloc[-1]
        fmt = market.format_price
        ma_text = f"{self.short}일선 {fmt(s)}, {self.long}일선 {fmt(lo)}"

        if position is None:
            # 마지막 날 위에 있고 그 전 entry_window일 중 아래였던 날이 있으면, 그 사이에 교차했다
            crossed_recently = not above.iloc[-(self.entry_window + 1) : -1].all()
            if above.iloc[-1] and crossed_recently and self._market_ok(context):
                return self._signal(market, symbol, Side.BUY, bars, f"골든크로스: {ma_text}")
            return None

        last = Decimal(str(close.iloc[-1]))
        stop_price = position.avg_price * (1 - self.stop_loss)
        if last <= stop_price:
            reason = f"손절: 종가 {fmt(last)} ≤ 손절가 {fmt(stop_price)}"
            return self._signal(market, symbol, Side.SELL, bars, reason, stop_loss=True)

        if self.trailing_stop is not None:
            entry_day = pd.Timestamp(trading_date(market, position.opened_at))
            held = close[close.index >= entry_day]
            if not held.empty:
                peak = Decimal(str(held.max()))
                if last <= peak * (1 - self.trailing_stop):
                    reason = f"고점 대비 하락: 고점 {fmt(peak)}, 종가 {fmt(last)}"
                    return self._signal(market, symbol, Side.SELL, bars, reason, stop_loss=True)

        if s < lo:
            return self._signal(market, symbol, Side.SELL, bars, f"데드크로스: {ma_text}")
        return None

    def _market_ok(self, context: MarketContext) -> bool:
        """시장 필터: 지수가 market_filter일 이동평균 위일 때만 산다. 지수를 모르면 사지 않는다."""
        if self.market_filter is None:
            return True
        index = context.index_bars
        if index is None or len(index) < self.market_filter:
            return False
        index_close = index["close"]
        return bool(index_close.iloc[-1] > index_close.tail(self.market_filter).mean())
