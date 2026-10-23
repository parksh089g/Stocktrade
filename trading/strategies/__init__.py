"""매매 전략."""

from trading.strategies.base import Strategy
from trading.strategies.rsi_rebound import RsiRebound
from trading.strategies.trend_ma import TrendMA

STRATEGIES: dict[str, type[Strategy]] = {TrendMA.id: TrendMA, RsiRebound.id: RsiRebound}


def create_strategy(name: str) -> Strategy:
    """기본 설정으로 전략을 만든다."""
    try:
        return STRATEGIES[name]()
    except KeyError:
        raise ValueError(f"알 수 없는 전략: {name} (가능: {', '.join(STRATEGIES)})") from None


__all__ = ["STRATEGIES", "RsiRebound", "Strategy", "TrendMA", "create_strategy"]
