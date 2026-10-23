"""② 단기 반등: RSI 과매도 탈출.

- 매수: RSI가 oversold 아래에 있다가 다시 위로 올라온 날.
- 매도(먼저 걸리는 것): 종가가 평균단가 대비 stop_loss 이상 하락(손절),
  RSI가 exit_level 이상 회복, 보유 max_hold_days일 경과.
"""

from decimal import Decimal

import pandas as pd

from trading.core.clock import trading_date
from trading.core.models import Market, Position, Side, Signal
from trading.strategies.base import MarketContext, Strategy
from trading.strategies.indicators import rsi


class RsiRebound(Strategy):
    id = "rsi_rebound"

    def __init__(
        self,
        period: int = 14,
        oversold: float = 30.0,
        exit_level: float = 50.0,
        stop_loss: float = 0.05,
        max_hold_days: int = 10,
    ) -> None:
        if period < 2:
            raise ValueError("RSI 기간은 2 이상이어야 합니다")
        if not 0 < oversold < exit_level < 100:
            raise ValueError("0 < oversold < exit_level < 100 이어야 합니다")
        if not 0 < stop_loss < 1:
            raise ValueError("stop_loss는 0과 1 사이 비율이어야 합니다")
        if max_hold_days < 1:
            raise ValueError("max_hold_days는 1 이상이어야 합니다")
        self.period = period
        self.oversold = oversold
        self.exit_level = exit_level
        self.stop_loss = Decimal(str(stop_loss))
        self.max_hold_days = max_hold_days

    @property
    def lookback(self) -> int:
        # 지수평활 RSI가 안정되도록 기간의 7배 (14일 → 98개)
        return self.period * 7

    def _evaluate(
        self,
        market: Market,
        symbol: str,
        bars: pd.DataFrame,
        position: Position | None,
        context: MarketContext,
    ) -> Signal | None:
        r = rsi(bars["close"], self.period)
        r_prev, r_now = r.iloc[-2], r.iloc[-1]

        if position is None:
            if r_prev < self.oversold <= r_now:
                reason = f"RSI 과매도 탈출: {r_prev:.1f} → {r_now:.1f}"
                return self._signal(market, symbol, Side.BUY, bars, reason)
            return None

        close = Decimal(str(bars["close"].iloc[-1]))
        stop_price = position.avg_price * (1 - self.stop_loss)
        if close <= stop_price:
            fmt = market.format_price
            reason = f"손절: 종가 {fmt(close)} ≤ 손절가 {fmt(stop_price)}"
            return self._signal(market, symbol, Side.SELL, bars, reason, stop_loss=True)

        if r_now >= self.exit_level:
            return self._signal(market, symbol, Side.SELL, bars, f"RSI 회복: {r_now:.1f}")

        entry_day = pd.Timestamp(trading_date(market, position.opened_at))
        held_days = int((bars.index >= entry_day).sum())
        if held_days >= self.max_hold_days:
            return self._signal(market, symbol, Side.SELL, bars, f"보유 {held_days}일 경과")
        return None
