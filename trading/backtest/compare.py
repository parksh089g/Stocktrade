"""개선안 비교: 앞 기간에서 설정을 고르고, 보지 않은 뒤 기간에서 확인한다.

  uv run python -m trading.backtest.compare                    # 저장된 KIS 일봉(data/bars) 사용
  uv run python -m trading.backtest.compare --split 2024-01-01

고르는 기준(결과를 보기 전에 정했다): 앞 기간의 '연수익률 ÷ |최대낙폭|'이 가장 큰 설정.
두 기간은 각각 새 계좌로 시작한다. 뒤 기간 결과는 고르는 데 쓰지 않는다.

후보
- 전략 배분: 추세/반등 50/50(지금), 70/30, 80/20, 추세만
- +ETF: 전략이 쓰지 않는 현금을 지수 ETF(국내 KODEX 200, 미국 SPY)로 굴린다(시험용)
"""

import argparse
import math
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from trading.backtest.__main__ import BENCHMARKS, PORTFOLIO_CASH
from trading.backtest.data import fetch_bars_cached
from trading.backtest.portfolio import PortfolioResult, cagr, combine, run_portfolio_backtest
from trading.backtest.simulator import max_drawdown
from trading.brokers.kis import KisBroker
from trading.config import Settings
from trading.core.log import setup_logging
from trading.core.models import Market
from trading.engine.factory import SetupError, build_kis_broker
from trading.risk.config import RiskConfig
from trading.strategies import create_strategy
from trading.universe import load_universe

WEIGHTS = {
    "50/50": {"trend_ma": 0.5, "rsi_rebound": 0.5},
    "70/30": {"trend_ma": 0.7, "rsi_rebound": 0.3},
    "80/20": {"trend_ma": 0.8, "rsi_rebound": 0.2},
    "추세만": {"trend_ma": 1.0},
}


@dataclass(frozen=True)
class Config:
    weights: str
    park: bool

    @property
    def label(self) -> str:
        return self.weights + (" +ETF" if self.park else "")


CONFIGS = [Config(w, park) for park in (False, True) for w in WEIGHTS]
BASELINE = Config("50/50", False)


@dataclass(frozen=True)
class Score:
    cagr: float
    mdd: float
    invested: float = 1.0
    parked: float = 0.0

    @property
    def ratio(self) -> float:
        return self.cagr / abs(self.mdd) if self.mdd else math.inf


def run_config(
    market: Market,
    bars: dict[str, pd.DataFrame],
    instruments,
    park: pd.DataFrame,
    config: Config,
    *,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
    cash: float,
) -> tuple[Score, PortfolioResult]:
    weights = WEIGHTS[config.weights]
    result = run_portfolio_backtest(
        market,
        bars,
        instruments,
        [create_strategy(name) for name in weights],
        initial_cash=cash,
        risk=RiskConfig(strategy_weights={k: Decimal(str(v)) for k, v in weights.items()}),
        start=start,
        end=end,
        park=park if config.park else None,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m trading.backtest.compare")
    parser.add_argument("--days", type=int, default=1500)
    parser.add_argument("--split", default="2024-01-01", help="뒤 기간 시작일")
    parser.add_argument("--market", choices=[m.value for m in Market])
    args = parser.parse_args(argv)

    settings = Settings()
    setup_logging(settings.secret_values())
    universe = load_universe(settings.universe_path)
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
            broker.add_instrument(bench)
        instruments = universe[market]
        bars = {
            i.symbol: fetch_bars_cached(broker, market, i.symbol, args.days)[0] for i in instruments
        }
        park = fetch_bars_cached(broker, market, bench.symbol, args.days)[0]
        cash = PORTFOLIO_CASH[market]

        results = {}
        for config in CONFIGS:
            print(f"  {market.label} {config.label} 계산 중...", flush=True)
            before = run_config(market, bars, instruments, park, config, start=None,
                                end=split - pd.Timedelta(days=1), cash=cash)  # fmt: skip
            after = run_config(market, bars, instruments, park, config, start=split, end=None,
                               cash=cash)  # fmt: skip
            results[config] = (before, after)

        any_before, any_after = results[BASELINE][0][1], results[BASELINE][1][1]
        idx_before = index_score(park["close"], any_before.equity.index)
        idx_after = index_score(park["close"], any_after.equity.index)
        best = max(CONFIGS, key=lambda c: results[c][0][0].ratio)

        d0, d1 = any_before.equity.index[[0, -1]]
        e0, e1 = any_after.equity.index[[0, -1]]
        print(f"\n[{market.label}] 처음 {market.format_price(cash)} {market.currency}")
        print(
            f"  앞 기간 {d0.date()}~{d1.date()} (고르기)  /  뒤 기간 {e0.date()}~{e1.date()} (확인)"
        )
        print(f"  {'설정':<12} {'앞:연수익':>8} {'낙폭':>7} {'점수':>5} | "
              f"{'뒤:연수익':>8} {'낙폭':>7} {'점수':>5} | 주식 ETF(뒤 평균)")  # fmt: skip
        for config in CONFIGS:
            (sb, _), (sa, _) = results[config]
            mark = " ◀ 고름" if config == best else ""
            print(f"  {config.label:<12} {_fmt(sb)} | {_fmt(sa)} | "
                  f"{sa.invested:4.0%} {sa.parked:4.0%}{mark}")  # fmt: skip
        print(f"  {bench.name + ' 보유':<12} {_fmt(idx_before)} | {_fmt(idx_after)}")
        chosen[market] = (results[best][1][1], results[BASELINE][1][1], park["close"])

    if len(chosen) == 2:
        print("\n[국내 50% + 미국 50%] 뒤 기간, 환율 변화 제외")
        best_curve = combine([chosen[m][0] for m in Market], [0.5, 0.5])
        base_curve = combine([chosen[m][1] for m in Market], [0.5, 0.5])
        index_curves = []
        for m in Market:
            close = chosen[m][2].loc[chosen[m][0].equity.index[0] :]
            index_curves.append(close / close.iloc[0] * 0.5)
        index_curve = (
            pd.concat(index_curves, axis=1, sort=True).sort_index().ffill().fillna(0.5).sum(axis=1)
        )
        for name, curve in [("고른 설정", best_curve), ("지금(50/50)", base_curve),
                            ("지수 반반", index_curve)]:  # fmt: skip
            print(f"  {name:<10} 연 {cagr(curve, 1.0):+.1%}  최대낙폭 {max_drawdown(curve):.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
