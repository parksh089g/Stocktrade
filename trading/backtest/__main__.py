"""백테스트 실행.

예)
  uv run python -m trading.backtest                       # 가짜 데이터로 두 전략 모두
  uv run python -m trading.backtest --csv 005930.csv --market KR --strategy trend_ma
  uv run python -m trading.backtest --source kis           # 종목 목록 전체를 KIS 실제 일봉으로
  uv run python -m trading.backtest --source kis --market US --days 500 --trades
  uv run python -m trading.backtest --source kis --portfolio --days 1500   # 계좌 전체

KIS 일봉은 data/bars/에 저장해 두고 다시 쓴다(--refresh로 새로 받기).
"""

import argparse

from trading.backtest.data import fetch_bars_cached, load_bars_csv
from trading.backtest.portfolio import (
    PortfolioResult,
    cagr,
    combine,
    format_portfolio,
    run_portfolio_backtest,
    yearly_returns,
)
from trading.backtest.simulator import (
    BacktestResult,
    format_summary,
    format_table,
    max_drawdown,
    run_backtest,
)
from trading.brokers.base import BrokerAdapter
from trading.brokers.fake import make_random_bars
from trading.brokers.kis import KisBroker
from trading.config import Settings
from trading.core.log import setup_logging
from trading.core.models import Instrument, Market
from trading.engine.factory import SetupError, build_kis_broker
from trading.strategies import STRATEGIES, create_strategy
from trading.universe import load_universe

# 2천만원 계좌에서 한 번 매수하는 금액(전략 50% × 4종목 중 1) 정도로 시작한다
DEFAULT_CASH = {Market.KR: 2_500_000.0, Market.US: 1_800.0}
# 계좌 전체 백테스트: 2천만원(미국은 1달러 1,400원대로 잡은 비슷한 금액)
PORTFOLIO_CASH = {Market.KR: 20_000_000.0, Market.US: 14_000.0}
BENCHMARKS = {
    Market.KR: Instrument(market=Market.KR, symbol="069500", name="KODEX 200"),
    Market.US: Instrument(market=Market.US, symbol="SPY", name="S&P500(SPY)", exchange="AMEX"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m trading.backtest")
    parser.add_argument("--source", choices=["random", "csv", "kis"])
    parser.add_argument("--strategy", default="all", choices=["all", *STRATEGIES])
    parser.add_argument("--csv", help="일봉 CSV 경로")
    parser.add_argument("--market", choices=[m.value for m in Market])
    parser.add_argument("--symbol", help="종목 코드(kis면 이 종목만)")
    parser.add_argument("--cash", type=float, help="처음 현금(기본: 국내 250만원, 미국 1,800달러)")
    parser.add_argument("--days", type=int, default=750, help="일봉 개수")
    parser.add_argument("--seed", type=int, default=0, help="가짜 데이터 시드")
    parser.add_argument("--refresh", action="store_true", help="kis: 저장된 일봉 대신 새로 받기")
    parser.add_argument("--trades", action="store_true", help="거래 내역도 출력")
    parser.add_argument(
        "--portfolio", action="store_true", help="kis: 종목 목록 전체로 계좌 단위 백테스트"
    )
    args = parser.parse_args(argv)

    source = args.source or ("csv" if args.csv else "random")
    strategies = list(STRATEGIES) if args.strategy == "all" else [args.strategy]
    if source == "kis":
        return _run_kis(args, strategies)

    market = Market(args.market or "KR")
    if source == "csv":
        bars = load_bars_csv(args.csv)
        symbol = args.symbol or args.csv
    else:
        bars = make_random_bars(args.days, seed=args.seed)
        symbol = args.symbol or f"RANDOM(seed={args.seed})"
        print("※ 가짜 데이터(랜덤워크)라 수익률에 의미는 없습니다. 동작 확인용입니다.\n")
    cash = args.cash or DEFAULT_CASH[market]
    for name in strategies:
        result = run_backtest(
            create_strategy(name), bars, market=market, symbol=symbol, initial_cash=cash
        )
        print(format_summary(result))
        if args.trades:
            _print_trades(result)
        print()
    return 0


def _run_kis(args: argparse.Namespace, strategies: list[str]) -> int:
    settings = Settings()
    setup_logging(settings.secret_values())
    universe = load_universe(settings.universe_path)
    try:
        broker = build_kis_broker(settings, universe)
    except SetupError as e:
        print(f"설정 오류: {e}")
        return 2

    markets = [Market(args.market)] if args.market else list(Market)
    if args.portfolio:
        return _run_portfolio(broker, universe, markets, args)
    for market in markets:
        instruments = [i for i in universe[market] if not args.symbol or i.symbol == args.symbol]
        names = {i.symbol: i.name for i in instruments}
        data = {}
        for inst in instruments:
            bars, cached = fetch_bars_cached(
                broker, market, inst.symbol, args.days, refresh=args.refresh
            )
            data[inst.symbol] = bars
            note = "저장본" if cached else "KIS에서 받음"
            print(
                f"  {inst.symbol} {len(bars)}개 {bars.index[0].date()} ~ {bars.index[-1].date()}"
                f" ({note})"
            )
        cash = args.cash or DEFAULT_CASH[market]
        print(f"\n[{market.label}] 처음 현금 {market.format_price(cash)} {market.currency}, 종목별")
        for name in strategies:
            results = [
                run_backtest(
                    create_strategy(name), bars, market=market, symbol=symbol, initial_cash=cash
                )
                for symbol, bars in data.items()
            ]
            print(f"\n({name})")
            print(format_table(results, names))
            if args.trades:
                for r in results:
                    print(f"\n  {r.symbol} {names.get(r.symbol, '')}")
                    _print_trades(r)
        print()
    return 0


def _run_portfolio(
    broker: BrokerAdapter, universe, markets: list[Market], args: argparse.Namespace
) -> int:
    results: list[PortfolioResult] = []
    for market in markets:
        instruments = universe[market]
        bench = BENCHMARKS[market]
        if isinstance(broker, KisBroker):
            broker.add_instrument(bench)
        bars = {
            i.symbol: fetch_bars_cached(broker, market, i.symbol, args.days, refresh=args.refresh)[
                0
            ]
            for i in instruments
        }
        bench_bars = fetch_bars_cached(broker, market, bench.symbol, args.days)[0]
        result = run_portfolio_backtest(
            market,
            bars,
            instruments,
            [create_strategy(name) for name in STRATEGIES],
            initial_cash=args.cash or PORTFOLIO_CASH[market],
        )
        results.append(result)
        print(format_portfolio(result, bench_bars["close"], bench.name))
        print()
    if len(results) == 2:
        curve = combine(results, [0.5, 0.5])
        yearly = yearly_returns(curve, 1.0)
        print("[국내 50% + 미국 50%] 환율 변화 제외")
        print(
            f"  총수익 {curve.iloc[-1] - 1:+.1%}  연 {cagr(curve, 1.0):+.1%}  "
            f"최대낙폭 {max_drawdown(curve):.1%}"
        )
        print("  연도별 " + "  ".join(f"{y} {v:+.1%}" for y, v in yearly.items()))
    return 0


def _print_trades(result: BacktestResult) -> None:
    fmt = result.market.format_price
    for t in result.trades:
        print(
            f"    {t.entry_date} {fmt(t.entry_price)} → {t.exit_date} {fmt(t.exit_price)}  "
            f"{t.return_pct:+.2%}  ({t.exit_reason})"
        )


if __name__ == "__main__":
    raise SystemExit(main())
