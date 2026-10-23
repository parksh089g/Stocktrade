"""한 종목, 한 전략 백테스트.

실제 운영과 같은 조건으로 돌린다.
- 매일 종가까지 확정된 일봉 lookback개만 전략에 넘긴다(미래 데이터를 보지 않는다).
- 신호는 다음 날 시가에 체결한다.
- 자금은 전부 그 종목에 쓴다(전략별 배분과 종목당 한도는 M3 리스크 단계에서 다룬다).
- 마지막 날 보유 중이면 종가로 정리해서 거래 기록에 넣는다.
"""

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

import pandas as pd

from trading.core.models import Market, Position, Side, Signal, validate_bars
from trading.strategies.base import Strategy


@dataclass(frozen=True)
class Costs:
    fee_rate: float  # 매수/매도 각각 붙는 수수료율
    sell_tax_rate: float  # 매도할 때만 붙는 세금


# 대략적인 값. 실제 계좌의 수수료/세율을 확인해서 맞추는 것이 좋다.
DEFAULT_COSTS = {
    Market.KR: Costs(fee_rate=0.00015, sell_tax_rate=0.002),
    Market.US: Costs(fee_rate=0.0025, sell_tax_rate=0.0),
}


@dataclass(frozen=True)
class Trade:
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    qty: int
    pnl: float  # 수수료와 세금을 뺀 손익
    return_pct: float  # 매수에 쓴 돈 대비 손익 비율
    exit_reason: str


@dataclass(frozen=True)
class Metrics:
    total_return: float
    cagr: float
    max_drawdown: float  # 음수. 예: -0.25 = 고점 대비 25% 하락
    num_trades: int
    win_rate: float
    avg_trade_return: float
    exposure: float  # 보유 중이던 날의 비율
    buy_and_hold_return: float


@dataclass(frozen=True)
class BacktestResult:
    strategy: str
    market: Market
    symbol: str
    initial_cash: float
    equity: pd.Series  # 날짜별 평가금액(종가 기준)
    trades: list[Trade]
    metrics: Metrics


@dataclass
class _Holding:
    qty: int
    entry_date: date
    entry_price: float
    cost: float  # 수수료 포함 매수 금액


def run_backtest(
    strategy: Strategy,
    bars: pd.DataFrame,
    *,
    market: Market,
    symbol: str = "TEST",
    initial_cash: float = 10_000_000,
    costs: Costs | None = None,
) -> BacktestResult:
    validate_bars(bars)
    costs = costs or DEFAULT_COSTS[market]
    lookback = strategy.lookback
    last = len(bars) - 1

    cash = float(initial_cash)
    holding: _Holding | None = None
    pending: Signal | None = None
    trades: list[Trade] = []
    equity: list[float] = []
    held_flags: list[bool] = []

    def sell(day: date, price: float, reason: str) -> None:
        nonlocal cash, holding
        assert holding is not None
        proceeds = holding.qty * price * (1 - costs.fee_rate - costs.sell_tax_rate)
        cash += proceeds
        pnl = proceeds - holding.cost
        trades.append(
            Trade(
                entry_date=holding.entry_date,
                entry_price=holding.entry_price,
                exit_date=day,
                exit_price=price,
                qty=holding.qty,
                pnl=pnl,
                return_pct=pnl / holding.cost,
                exit_reason=reason,
            )
        )
        holding = None

    for i in range(len(bars)):
        day = bars.index[i].date()
        open_, close = float(bars["open"].iloc[i]), float(bars["close"].iloc[i])

        # 1) 전날 신호를 오늘 시가에 체결
        if pending is not None:
            if pending.side is Side.BUY and holding is None:
                qty = math.floor(cash / (open_ * (1 + costs.fee_rate)))
                if qty > 0:
                    cost = qty * open_ * (1 + costs.fee_rate)
                    cash -= cost
                    holding = _Holding(qty=qty, entry_date=day, entry_price=open_, cost=cost)
            elif pending.side is Side.SELL and holding is not None:
                sell(day, open_, pending.reason)
            pending = None

        # 2) 마지막 날 남은 보유분은 종가로 정리
        if i == last and holding is not None:
            sell(day, close, "백테스트 종료")

        held_flags.append(holding is not None)
        equity.append(cash + (holding.qty * close if holding else 0.0))

        # 3) 오늘 종가까지 보고 내일 체결할 신호를 만든다
        if i < last and i + 1 >= lookback:
            window = bars.iloc[i + 1 - lookback : i + 1]
            pending = strategy.evaluate(
                market, symbol, window, _position(strategy, market, symbol, holding)
            )

    equity_series = pd.Series(equity, index=bars.index, name="equity")
    return BacktestResult(
        strategy=strategy.id,
        market=market,
        symbol=symbol,
        initial_cash=float(initial_cash),
        equity=equity_series,
        trades=trades,
        metrics=compute_metrics(equity_series, trades, held_flags, bars, float(initial_cash)),
    )


def _position(
    strategy: Strategy, market: Market, symbol: str, holding: _Holding | None
) -> Position | None:
    if holding is None:
        return None
    return Position(
        market=market,
        symbol=symbol,
        strategy=strategy.id,
        qty=holding.qty,
        avg_price=Decimal(str(holding.entry_price)),
        # 체결일 정오(현지)로 둔다. 전략은 이 시각의 현지 날짜만 쓴다.
        opened_at=datetime.combine(holding.entry_date, time(12), tzinfo=market.timezone),
    )


def max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1).min())


def compute_metrics(
    equity: pd.Series,
    trades: list[Trade],
    held_flags: list[bool],
    bars: pd.DataFrame,
    initial_cash: float,
) -> Metrics:
    total_return = float(equity.iloc[-1] / initial_cash - 1)
    years = len(equity) / 252
    cagr = (1 + total_return) ** (1 / years) - 1 if total_return > -1 else -1.0
    returns = [t.return_pct for t in trades]
    return Metrics(
        total_return=total_return,
        cagr=cagr,
        max_drawdown=max_drawdown(equity),
        num_trades=len(trades),
        win_rate=sum(r > 0 for r in returns) / len(returns) if returns else 0.0,
        avg_trade_return=sum(returns) / len(returns) if returns else 0.0,
        exposure=sum(held_flags) / len(held_flags),
        buy_and_hold_return=float(bars["close"].iloc[-1] / bars["close"].iloc[0] - 1),
    )


def format_summary(result: BacktestResult) -> str:
    m = result.metrics
    start, end = result.equity.index[0].date(), result.equity.index[-1].date()
    days = len(result.equity)
    lines = [
        f"[{result.strategy}] {result.market} {result.symbol}  {start} ~ {end} ({days}일)",
        f"  총수익률 {m.total_return:+.2%}  연환산 {m.cagr:+.2%}  최대낙폭 {m.max_drawdown:.2%}",
        f"  거래 {m.num_trades}회  승률 {m.win_rate:.0%}  평균 {m.avg_trade_return:+.2%}"
        f"  보유비중 {m.exposure:.0%}",
        f"  같은 기간 그냥 보유했다면 {m.buy_and_hold_return:+.2%}",
    ]
    return "\n".join(lines)


def format_table(results: list[BacktestResult], names: dict[str, str] | None = None) -> str:
    """여러 종목 결과를 표로. 마지막 줄은 종목 평균(같은 금액씩 나눠 넣었다고 본 단순 평균)."""
    names = names or {}
    header = (
        f"{'종목':<8} {'총수익':>8} {'연환산':>8} {'최대낙폭':>8} {'거래':>4} {'승률':>5} "
        f"{'보유비중':>7} {'그냥보유':>8}  이름"
    )
    lines = [header]
    for r in results:
        m = r.metrics
        lines.append(
            f"{r.symbol:<8} {m.total_return:>+8.1%} {m.cagr:>+8.1%} {m.max_drawdown:>8.1%} "
            f"{m.num_trades:>4} {m.win_rate:>5.0%} {m.exposure:>7.0%} "
            f"{m.buy_and_hold_return:>+8.1%}  {names.get(r.symbol, '')}"
        )
    if len(results) > 1:
        n = len(results)

        def avg(f):
            return sum(f(r.metrics) for r in results) / n

        lines.append(
            f"{'평균':<8} {avg(lambda m: m.total_return):>+8.1%} {avg(lambda m: m.cagr):>+8.1%} "
            f"{avg(lambda m: m.max_drawdown):>8.1%} {avg(lambda m: m.num_trades):>4.0f} "
            f"{avg(lambda m: m.win_rate):>5.0%} {avg(lambda m: m.exposure):>7.0%} "
            f"{avg(lambda m: m.buy_and_hold_return):>+8.1%}"
        )
    return "\n".join(lines)
