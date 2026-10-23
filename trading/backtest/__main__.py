"""백테스트 실행.

예)
  uv run python -m trading.backtest                       # 가짜 데이터로 두 전략 모두
  uv run python -m trading.backtest --csv 005930.csv --market KR --strategy trend_ma
"""

import argparse

from trading.backtest.data import load_bars_csv
from trading.backtest.simulator import format_summary, run_backtest
from trading.brokers.fake import make_random_bars
from trading.core.models import Market
from trading.strategies import STRATEGIES, create_strategy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m trading.backtest")
    parser.add_argument("--strategy", default="all", choices=["all", *STRATEGIES])
    parser.add_argument("--csv", help="일봉 CSV 경로. 없으면 가짜 데이터를 쓴다")
    parser.add_argument("--market", default="KR", choices=[m.value for m in Market])
    parser.add_argument("--symbol", help="표시용 종목 코드")
    parser.add_argument("--cash", type=float, default=10_000_000)
    parser.add_argument("--days", type=int, default=750, help="가짜 데이터 일수")
    parser.add_argument("--seed", type=int, default=0, help="가짜 데이터 시드")
    parser.add_argument("--trades", action="store_true", help="거래 내역도 출력")
    args = parser.parse_args(argv)

    market = Market(args.market)
    if args.csv:
        bars = load_bars_csv(args.csv)
        symbol = args.symbol or args.csv
    else:
        bars = make_random_bars(args.days, seed=args.seed)
        symbol = args.symbol or f"RANDOM(seed={args.seed})"
        print("※ 가짜 데이터(랜덤워크)라 수익률에 의미는 없습니다. 동작 확인용입니다.\n")

    names = list(STRATEGIES) if args.strategy == "all" else [args.strategy]
    for name in names:
        result = run_backtest(
            create_strategy(name), bars, market=market, symbol=symbol, initial_cash=args.cash
        )
        print(format_summary(result))
        if args.trades:
            for t in result.trades:
                print(
                    f"    {t.entry_date} {market.format_price(t.entry_price)} → "
                    f"{t.exit_date} {market.format_price(t.exit_price)}  "
                    f"{t.return_pct:+.2%}  ({t.exit_reason})"
                )
        print()


if __name__ == "__main__":
    main()
