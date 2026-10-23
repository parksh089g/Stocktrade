"""전략 인터페이스.

전략은 상태를 갖지 않는다. 엔진과 백테스트가 똑같이 최근 일봉 lookback개와
그 전략의 보유분을 넘겨주면, 전략은 매수/매도 신호를 내거나 None을 돌려준다.
수량과 자금 배분은 전략이 아니라 리스크 단계에서 정한다.
"""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import ClassVar

import pandas as pd

from trading.core.models import Market, Position, Side, Signal


class Strategy(ABC):
    id: ClassVar[str]

    @property
    @abstractmethod
    def lookback(self) -> int:
        """evaluate()에 넘길 일봉 개수. 이보다 적으면 신호를 내지 않는다.

        KIS 일봉 조회 한 번에 받는 개수(100개) 안에 들도록 둔다.
        """

    def evaluate(
        self, market: Market, symbol: str, bars: pd.DataFrame, position: Position | None
    ) -> Signal | None:
        """마지막 행까지의 완성된 일봉을 보고 신호를 낸다.

        bars의 마지막 행은 확정된 일봉이어야 한다(장중 미완성 봉은 엔진이 빼고 넘긴다).
        position은 이 전략이 들고 있는 그 종목 보유분, 없으면 None.
        """
        if position is not None and position.strategy != self.id:
            raise ValueError(f"{self.id} 전략에 {position.strategy} 전략의 보유분이 넘어왔습니다")
        if len(bars) < self.lookback:
            return None
        # 더 많이 넘어와도 lookback개만 쓴다. 지수평활 지표가 넘겨받은 길이에 따라 달라지지 않게.
        return self._evaluate(market, symbol, bars.tail(self.lookback), position)

    @abstractmethod
    def _evaluate(
        self, market: Market, symbol: str, bars: pd.DataFrame, position: Position | None
    ) -> Signal | None: ...

    def _signal(
        self,
        market: Market,
        symbol: str,
        side: Side,
        bars: pd.DataFrame,
        reason: str,
        *,
        stop_loss: bool = False,
    ) -> Signal:
        return Signal(
            market=market,
            symbol=symbol,
            side=side,
            strategy=self.id,
            price=Decimal(str(bars["close"].iloc[-1])),
            bar_date=bars.index[-1].date(),
            reason=reason,
            stop_loss=stop_loss,
        )
