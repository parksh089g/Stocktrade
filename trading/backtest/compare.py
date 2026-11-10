"""개선안 비교: 앞 기간에서 설정을 고르고, 보지 않은 뒤 기간에서 확인한다.

  uv run python -m trading.backtest.compare                    # 이번 시험(cd), 저장된 KIS 일봉 사용
  uv run python -m trading.backtest.compare --experiment ab    # 지난 시험(배분, ETF 굴리기)
  uv run python -m trading.backtest.compare --split 2024-01-01

고르는 기준(결과를 보기 전에 정했다): 앞 기간의 '연수익률 ÷ |최대낙폭|'이 가장 큰 설정.
두 기간은 각각 새 계좌로 시작한다. 뒤 기간 결과는 고르는 데 쓰지 않는다.

시험 ab (2026-10-23)
- 전략 배분: 추세/반등 50/50(지금), 70/30, 80/20, 추세만
- +ETF: 전략이 쓰지 않는 현금을 지수 ETF(국내 KODEX 200, 미국 SPY)로 굴린다
시험 cd (2026-10-23, 숫자는 미리 고정)
- C1 고점손절: 추세 추종이 산 뒤 최고 종가에서 15% 떨어지면 판다
- C2 시장필터: 지수 ETF가 60일선 아래면 추세 추종이 새로 사지 않는다
- D 종목확대: 국내/미국 각 20종목(config/universe_wide.toml)
"""

import argparse
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

import pandas as pd

from trading.backtest.__main__ import PORTFOLIO_CASH
from trading.backtest.data import fetch_bars_cached
from trading.backtest.portfolio import PortfolioResult, cagr, combine, run_portfolio_backtest
from trading.backtest.simulator import max_drawdown
from trading.brokers.kis import KisBroker
from trading.config import Settings
from trading.core.log import setup_logging
from trading.core.models import Instrument, Market
from trading.engine.factory import SetupError, build_kis_broker
from trading.risk.config import RiskConfig
from trading.strategies import RsiRebound, TrendMA
from trading.strategies.base import Strategy
from trading.universe import BENCHMARKS, load_universe

BOTH = {"trend_ma": 0.5, "rsi_rebound": 0.5}


def _base() -> list[Strategy]:
    return [TrendMA(), RsiRebound()]


@dataclass(frozen=True)
class Config:
    label: str
    strategies: Callable[[], list[Strategy]] = _base
    weights: dict[str, float] = field(default_factory=lambda: dict(BOTH))
    park: bool = False  # 남는 현금을 지수 ETF로
    wide: bool = False  # 종목 확대 목록 사용
    index: bool = False  # 엔진에 시장 지수 일봉을 넘김(시장 필터용)


def _weights(name: str, trend: float, rebound: float, park: bool) -> Config:
    w = {"trend_ma": trend} | ({"rsi_rebound": rebound} if rebound else {})

    def make() -> list[Strategy]:
        return [TrendMA()] + ([RsiRebound()] if rebound else [])

    return Config(label=name + (" +ETF" if park else ""), strategies=make, weights=w, park=park)


EXPERIMENTS: dict[str, list[Config]] = {
    "ab": [
        _weights(name, t, r, park)
        for park in (False, True)
        for name, t, r in [
            ("50/50", 0.5, 0.5),
            ("70/30", 0.7, 0.3),
            ("80/20", 0.8, 0.2),
            ("추세만", 1.0, 0.0),
        ]
    ],
    "cd": [
        Config("기준(지금)"),
        Config("C1 고점손절", lambda: [TrendMA(trailing_stop=0.15), RsiRebound()]),
        Config("C2 시장필터", lambda: [TrendMA(market_filter=60), RsiRebound()], index=True),
        Config(
            "C1+C2",
            lambda: [TrendMA(trailing_stop=0.15, market_filter=60), RsiRebound()],
            index=True,
        ),
        Config("D 종목확대", wide=True),
        Config(
            "D+C1+C2",
            lambda: [TrendMA(trailing_stop=0.15, market_filter=60), RsiRebound()],
            wide=True,
            index=True,
        ),
    ],
}


@dataclass(frozen=True)
class Score:
    cagr: float
    mdd: float
    invested: float = 1.0
    parked: float = 0.0

    @property
    def ratio(self) -> float:
        return self.cagr / abs(self.mdd) if self.mdd else math.inf


@dataclass
class MarketData:
    instruments: list[Instrument]
    wide_instruments: list[Instrument]
    bars: dict[str, pd.DataFrame]
    index: pd.DataFrame


def run_config(
    market: Market,
    data: MarketData,
    config: Config,
    *,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
    cash: float,
    index_symbol: str,
) -> tuple[Score, PortfolioResult]:
    instruments = data.wide_instruments if config.wide else data.instruments
    result = run_portfolio_backtest(
        market,
        {i.symbol: data.bars[i.symbol] for i in instruments},
        instruments,
        config.strategies(),
        initial_cash=cash,
        risk=RiskConfig(strategy_weights={k: Decimal(str(v)) for k, v in config.weights.items()}),
        start=start,
        end=end,
        park=data.index if config.park else None,
        index=(index_symbol, data.index) if config.index else None,
    )
    score = Score(
        cagr=cagr(result.equity, cash),
        mdd=max_drawdown(result.equity),
        invested=float(result.invested.mean()),
        parked=float(result.parked.mean()) if result.parked is not None else 0.0,
    )
    return score, result


def index_score(close: pd.Series, days: pd.DatetimeIndex) -> Score:
    window = close.loc[days[0] : days[-1]]
    return Score(cagr=cagr(window, float(window.iloc[0])), mdd=max_drawdown(window))


def _fmt(score: Score) -> str:
    return f"{score.cagr:+7.1%} {score.mdd:7.1%} {score.ratio:5.2f}"


def _load(broker, market: Market, instruments: list[Instrument], days: int) -> dict:
    bars = {}
    for inst in instruments:
        try:
            bars[inst.symbol] = fetch_bars_cached(broker, market, inst.symbol, days)[0]
        except Exception as e:
            print(f"  {inst.symbol} 일봉을 받지 못해 뺍니다: {e}")
    return bars


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m trading.backtest.compare")
    parser.add_argument("--experiment", default="cd", choices=list(EXPERIMENTS))
    parser.add_argument("--days", type=int, default=1500)
    parser.add_argument("--split", default="2024-01-01", help="뒤 기간 시작일")
    parser.add_argument("--market", choices=[m.value for m in Market])
    parser.add_argument("--wide-universe", default="config/universe_wide.toml")
    args = parser.parse_args(argv)

    settings = Settings()
    setup_logging(settings.secret_values())
    universe = load_universe(settings.universe_path)
    configs = EXPERIMENTS[args.experiment]
    wide = load_universe(args.wide_universe) if any(c.wide for c in configs) else universe
    try:
        broker = build_kis_broker(settings, universe)
    except SetupError as e:
        print(f"설정 오류: {e}")
        return 2

    split = pd.Timestamp(args.split)
    markets = [Market(args.market)] if args.market else list(Market)
    chosen: dict[Market, tuple[PortfolioResult, PortfolioResult, pd.Series]] = {}
    for market in markets:
        bench = BENCHMARKS[market]
        if isinstance(broker, KisBroker):
            for inst in [bench, *wide[market]]:
                broker.add_instrument(inst)
        everything = {i.symbol: i for i in [*universe[market], *wide[market]]}
        bars = _load(broker, market, list(everything.values()), args.days)
        data = MarketData(
            instruments=[i for i in universe[market] if i.symbol in bars],
            wide_instruments=[i for i in wide[market] if i.symbol in bars],
            bars=bars,
            index=fetch_bars_cached(broker, market, bench.symbol, args.days)[0],
        )
        cash = PORTFOLIO_CASH[market]

        results = {}
        for config in configs:
            print(f"  {market.label} {config.label} 계산 중...", flush=True)
            kw = {"cash": cash, "index_symbol": bench.symbol}
            before = run_config(market, data, config, start=None,
                                end=split - pd.Timedelta(days=1), **kw)  # fmt: skip
            after = run_config(market, data, config, start=split, end=None, **kw)
            results[config.label] = (before, after)

        base = configs[0].label
        first_before, first_after = results[base][0][1], results[base][1][1]
        idx_before = index_score(data.index["close"], first_before.equity.index)
        idx_after = index_score(data.index["close"], first_after.equity.index)
        best = max(configs, key=lambda c: results[c.label][0][0].ratio).label

        d0, d1 = first_before.equity.index[[0, -1]]
        e0, e1 = first_after.equity.index[[0, -1]]
        print(f"\n[{market.label}] 처음 {market.format_price(cash)} {market.currency}")
        print(
            f"  앞 기간 {d0.date()}~{d1.date()} (고르기)  /  뒤 기간 {e0.date()}~{e1.date()} (확인)"
        )
        print(f"  {'설정':<12} {'앞:연수익':>8} {'낙폭':>7} {'점수':>5} | "
              f"{'뒤:연수익':>8} {'낙폭':>7} {'점수':>5} | 주식 ETF(뒤 평균)")  # fmt: skip
        for config in configs:
            (sb, _), (sa, _) = results[config.label]
            mark = " ◀ 고름" if config.label == best else ""
            print(f"  {config.label:<12} {_fmt(sb)} | {_fmt(sa)} | "
                  f"{sa.invested:4.0%} {sa.parked:4.0%}{mark}")  # fmt: skip
        print(f"  {bench.name + ' 보유':<12} {_fmt(idx_before)} | {_fmt(idx_after)}")
        chosen[market] = (results[best][1][1], results[base][1][1], data.index["close"])

    if len(chosen) == 2:
        print("\n[국내 50% + 미국 50%] 뒤 기간, 환율 변화 제외")
        best_curve = combine([chosen[m][0] for m in Market], [0.5, 0.5])
        base_curve = combine([chosen[m][1] for m in Market], [0.5, 0.5])
        index_curves = []
        for m in Market:
            close = chosen[m][2].loc[chosen[m][0].equity.index[0] :]
            index_curves.append(close / close.iloc[0] * 0.5)
        index_curve = pd.concat(index_curves, axis=1, sort=True).ffill().fillna(0.5).sum(axis=1)
        for name, curve in [("고른 설정", best_curve), (configs[0].label, base_curve),
                            ("지수 반반", index_curve)]:  # fmt: skip
            print(f"  {name:<10} 연 {cagr(curve, 1.0):+.1%}  최대낙폭 {max_drawdown(curve):.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
