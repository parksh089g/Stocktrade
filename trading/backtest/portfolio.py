"""계좌 전체 백테스트: 실제 엔진(Engine)을 과거 날짜마다 그대로 돌린다.

신호, 전략별 배분, 한 번 매수 금액, 종목당 한도, 같은 날 같은 종목 1회, 손절, 지정가 규칙이
실제 운영과 같다. 하루 흐름:
1. 장 시작 전: 전날까지의 일봉만 보이게 하고 엔진을 돌린다. 현재가는 그날 시가라서
   시가로 체결될 수 있는 지정가 주문은 시가에 체결된다.
2. 장중: 그날 고가~저가 사이에 지정가가 있었으면 지정가로 체결된다.
3. 장 마감: 남은 주문은 만료되고, 종가로 계좌를 평가한다.

전략이 지표를 계산할 일봉(lookback)이 모이기 전 기간은 결과에서 뺀다. 그 기간은 매매를 못 하니
비교 지수도 같은 날부터 재야 공정하다.

park(지수 ETF)를 주면 전략이 쓰지 않는 현금을 그 ETF로 굴린다(시험용, 운영 엔진에는 없음).
장 마감 때 남은 현금으로 ETF를 사고, 전략이 살 때 현금이 모자라면 모자란 만큼 ETF를 판다.
ETF는 전략 보유 기록과 따로 관리하고, 잔고의 '주문 가능 현금'에 ETF 평가금액을 더해 보여 준다.

빠진 것: 거래량 한계(주문이 작다고 본다), 부분 체결, 환율(시장마다 따로 계산한다).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal

import pandas as pd

from trading.backtest.simulator import DEFAULT_COSTS, Costs, max_drawdown
from trading.brokers.base import BrokerError
from trading.brokers.fake import FakeBroker
from trading.core.models import (
    Balance,
    Instrument,
    Market,
    Order,
    OrderResult,
    OrderStatus,
    Side,
    validate_bars,
)
from trading.engine.report import RunStatus
from trading.engine.runner import Engine
from trading.notify.base import MemoryNotifier
from trading.risk.config import RiskConfig
from trading.storage.memory import MemoryStore
from trading.strategies.base import Strategy

# 실제 운영에서 엔진을 돌릴 시각(현지): 국내는 장 시작 전, 미국은 장 시작 직후
RUN_TIME = {Market.KR: time(8, 40), Market.US: time(9, 40)}


class HistoricalBroker(FakeBroker):
    """과거 일봉을 하루씩 보여 주는 가짜 증권사."""

    def __init__(
        self,
        market: Market,
        bars: dict[str, pd.DataFrame],
        cash: float,
        costs: Costs,
        park: pd.DataFrame | None = None,
    ) -> None:
        self._now = datetime.min
        super().__init__(
            cash={market: Decimal(str(cash))},
            fee_rate=Decimal(str(costs.fee_rate)),
            sell_tax_rate=Decimal(str(costs.sell_tax_rate)),
            clock=lambda: self._now,
        )
        self.market = market
        self._all = {symbol: validate_bars(df) for symbol, df in bars.items()}
        self.days: list[pd.Timestamp] = sorted(set().union(*(df.index for df in bars.values())))
        self._day_set = set(self.days)
        self._today = self.days[0]
        # 현금을 굴릴 ETF(없으면 None). ETF 매매는 수수료만 있고 매도 세금은 없다.
        self._park = validate_bars(park) if park is not None else None
        self._park_qty = 0
        self._park_price = Decimal(0)
        self._park_fee = Decimal(str(costs.fee_rate))

    def now(self) -> datetime:
        return self._now

    def start_day(self, day: pd.Timestamp) -> None:
        self._today = day
        self._now = datetime.combine(day.date(), RUN_TIME[self.market], self.market.timezone)
        for symbol, df in self._all.items():
            if day in df.index:
                self.set_price(self.market, symbol, str(df.at[day, "open"]))
        self._set_park_price("open")

    def end_day(self) -> None:
        for symbol, df in self._all.items():
            if self._today in df.index:
                row = df.loc[self._today]
                self.touch_open_orders(
                    self.market, symbol, Decimal(str(row["high"])), Decimal(str(row["low"]))
                )
                self.set_price(self.market, symbol, str(row["close"]))
        self.expire_open_orders(self.market)
        self._set_park_price("close")
        self._park_cash()

    def parked_value(self) -> Decimal:
        return self._park_qty * self._park_price

    def get_balance(self, market: Market) -> Balance:
        balance = super().get_balance(market)
        parked = self.parked_value()
        return Balance(
            market=market,
            cash=balance.cash + parked,
            total_equity=balance.total_equity + parked,
        )

    def _submit_order(self, order: Order) -> OrderResult:
        if self._park is not None and order.side is Side.BUY and self._park_qty:
            price = order.limit_price or self._prices[(order.market, order.symbol)]
            needed = order.qty * price * (1 + self._fee_rate)
            short = needed - (self._cash[self.market] - self._reserved_cash(self.market))
            if short > 0:
                per_share = self._park_price * (1 - self._park_fee)
                shares = min(self._park_qty, math.ceil(short / per_share))
                self._cash[self.market] += shares * per_share
                self._park_qty -= shares
        return super()._submit_order(order)

    def _set_park_price(self, column: str) -> None:
        if self._park is not None and self._today in self._park.index:
            self._park_price = Decimal(str(self._park.at[self._today, column]))

    def _park_cash(self) -> None:
        if self._park is None or self._park_price <= 0:
            return
        spare = self._cash[self.market] - self._reserved_cash(self.market)
        shares = int(spare / (self._park_price * (1 + self._park_fee)))
        if shares > 0:
            self._cash[self.market] -= shares * self._park_price * (1 + self._park_fee)
            self._park_qty += shares

    def is_trading_day(self, market: Market, day) -> bool:
        return pd.Timestamp(day) in self._day_set

    def get_daily_bars(self, market: Market, symbol: str, count: int) -> pd.DataFrame:
        df = self._all.get(symbol)
        if df is None:
            raise BrokerError(f"{symbol} 일봉이 없습니다")
        end = df.index.searchsorted(self._today)  # 오늘 봉은 아직 없다
        return df.iloc[max(0, end - count) : end].copy()


@dataclass
class PortfolioResult:
    market: Market
    initial_cash: float
    equity: pd.Series  # 날짜별 종가 기준 평가금액
    invested: pd.Series  # 날짜별 전략 보유 주식 비중(0~1)
    strategy_pnl: dict[str, float]  # 수수료/세금 빼기 전 전략별 손익
    strategy_buys: dict[str, int]
    filled: int
    expired: int
    rejected: int
    warnings: list[str] = field(default_factory=list)
    parked: pd.Series | None = None  # 날짜별 ETF 비중(현금 굴리기를 켰을 때)

    @property
    def total_return(self) -> float:
        return float(self.equity.iloc[-1] / self.initial_cash - 1)


def run_portfolio_backtest(
    market: Market,
    bars: dict[str, pd.DataFrame],
    instruments: Sequence[Instrument],
    strategies: Sequence[Strategy],
    *,
    initial_cash: float,
    costs: Costs | None = None,
    risk: RiskConfig | None = None,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    park: pd.DataFrame | None = None,
) -> PortfolioResult:
    """start~end 기간만 새 계좌로 돌린다(그 전 일봉은 지표 계산에만 쓴다)."""
    broker = HistoricalBroker(market, bars, initial_cash, costs or DEFAULT_COSTS[market], park=park)
    store = MemoryStore()
    engine = Engine(
        broker=broker,
        store=store,
        notifier=MemoryNotifier(),
        strategies=strategies,
        universe={m: list(instruments) if m is market else [] for m in Market},
        risk=risk,
        clock=broker.now,
    )
    warmup = max(s.lookback for s in strategies)
    days = [
        d
        for d in broker.days[warmup:]
        if (start is None or d >= start) and (end is None or d <= end)
    ]
    if not days:
        raise ValueError(f"일봉이 {warmup}개보다 많아야 하고 기간 안에 날짜가 있어야 합니다")
    equity, invested, parked, warnings = [], [], [], []
    for day in days:
        broker.start_day(day)
        report = engine.run(market)
        if report.status is RunStatus.ERROR:
            raise RuntimeError(f"{day.date()} 엔진 오류: {report.error}")
        warnings += [f"{day.date()} {w}" for w in report.warnings]
        broker.end_day()
        balance = broker.get_balance(market)
        total = float(balance.total_equity)
        equity.append(total)
        invested.append((total - float(balance.cash)) / total if total else 0.0)
        parked.append(float(broker.parked_value()) / total if total else 0.0)

    orders = store.all_orders()
    pnl: dict[str, float] = {s.id: 0.0 for s in strategies}
    buys: dict[str, int] = {s.id: 0 for s in strategies}
    for r in orders:
        if r.filled_qty and r.avg_fill_price:
            amount = float(r.filled_qty * r.avg_fill_price)
            sign = -1 if r.order.side is Side.BUY else 1
            pnl[r.order.strategy] = pnl.get(r.order.strategy, 0.0) + sign * amount
            if r.order.side is Side.BUY:
                buys[r.order.strategy] = buys.get(r.order.strategy, 0) + 1
    for p in store.get_positions(market):  # 마지막 날 들고 있는 것은 종가로 평가
        price = float(broker.get_price(market, p.symbol))
        pnl[p.strategy] = pnl.get(p.strategy, 0.0) + p.qty * price

    return PortfolioResult(
        market=market,
        initial_cash=initial_cash,
        equity=pd.Series(equity, index=pd.DatetimeIndex(days), name="equity"),
        invested=pd.Series(invested, index=pd.DatetimeIndex(days), name="invested"),
        strategy_pnl=pnl,
        strategy_buys=buys,
        filled=sum(r.status is OrderStatus.FILLED for r in orders),
        expired=sum(r.status is OrderStatus.EXPIRED for r in orders),
        rejected=sum(r.status is OrderStatus.REJECTED for r in orders),
        warnings=warnings,
        parked=pd.Series(parked, index=pd.DatetimeIndex(days), name="parked")
        if park is not None
        else None,
    )


def cagr(equity: pd.Series, initial: float) -> float:
    years = len(equity) / 252
    total = equity.iloc[-1] / initial
    return float(total ** (1 / years) - 1) if total > 0 else -1.0


def yearly_returns(equity: pd.Series, initial: float) -> pd.Series:
    """연도별 수익률. 첫해는 시작 금액 대비."""
    last = equity.groupby(equity.index.year).last()
    previous = last.shift(1).fillna(initial)
    return last / previous - 1


def combine(results: Sequence[PortfolioResult], weights: Sequence[float]) -> pd.Series:
    """시장별 계좌를 시작 비중대로 합친 수익 곡선(시작 = 1.0). 환율 변화는 넣지 않는다."""
    curves = [r.equity / r.initial_cash * w for r, w in zip(results, weights, strict=True)]
    joined = pd.concat(curves, axis=1, sort=True).ffill()
    for column, curve in zip(joined.columns, curves, strict=True):
        joined[column] = joined[column].fillna(float(curve.iloc[0]))  # 늦게 시작하는 쪽
    return joined.sum(axis=1)


def format_portfolio(
    result: PortfolioResult, benchmark: pd.Series | None = None, benchmark_name: str = ""
) -> str:
    m = result.market
    fmt = m.format_price
    eq = result.equity
    lines = [
        f"[{m.label} 계좌] {eq.index[0].date()} ~ {eq.index[-1].date()} ({len(eq)}일), "
        f"처음 {fmt(result.initial_cash)} {m.currency}",
        f"  최종 {fmt(eq.iloc[-1])}  총수익 {result.total_return:+.1%}  "
        f"연 {cagr(eq, result.initial_cash):+.1%}  최대낙폭 {max_drawdown(eq):.1%}",
        f"  평균 주식 비중 {result.invested.mean():.0%} (나머지는 현금)  "
        f"주문 체결 {result.filled} / 만료 {result.expired} / 거부 {result.rejected}",
        "  전략별 손익(수수료·세금 전): "
        + ", ".join(
            f"{name} {fmt(v)} (매수 {result.strategy_buys.get(name, 0)}회)"
            for name, v in result.strategy_pnl.items()
        ),
    ]
    yearly = yearly_returns(eq, result.initial_cash)
    if benchmark is not None:
        bench = benchmark.loc[eq.index[0] : eq.index[-1]]
        bench_total = float(bench.iloc[-1] / bench.iloc[0] - 1)
        lines.append(
            f"  비교: {benchmark_name} 그냥 보유 {bench_total:+.1%} "
            f"(연 {cagr(bench, float(bench.iloc[0])):+.1%}, 최대낙폭 {max_drawdown(bench):.1%})"
        )
        bench_yearly = yearly_returns(bench, float(bench.iloc[0]))
        lines.append("  연도별   계좌     " + benchmark_name)
        for year, value in yearly.items():
            lines.append(f"  {year}  {value:+7.1%}  {bench_yearly.get(year, float('nan')):+7.1%}")
    if result.warnings:
        lines.append(f"  경고 {len(result.warnings)}건 (예: {result.warnings[0]})")
    return "\n".join(lines)
