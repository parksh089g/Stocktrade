"""하루 실행.

예)
  uv run python -m trading.engine --market KR
  uv run python -m trading.engine --market US --ignore-hours   # fake 증권사로 장 시간 무시하고 확인

fake 증권사는 메모리 저장소와 화면 출력만 쓴다.
KIS(M5)는 Supabase 저장소와 웹 푸시(VAPID 키가 있으면)를 쓴다.
"""

import argparse
import sys

import pandas as pd

from trading.brokers.fake import make_demo_broker
from trading.config import Settings
from trading.core.clock import now_utc, trading_date
from trading.core.log import setup_logging
from trading.core.models import Market
from trading.engine.factory import build_notifier, build_store
from trading.engine.report import RunStatus
from trading.engine.runner import Engine
from trading.strategies import STRATEGIES, create_strategy
from trading.universe import load_universe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m trading.engine")
    parser.add_argument("--market", required=True, choices=[m.value for m in Market])
    parser.add_argument(
        "--ignore-hours",
        action="store_true",
        help="fake 증권사에서만: 휴장일/장 마감 검사를 건너뛴다",
    )
    parser.add_argument("--seed", type=int, default=0, help="fake 증권사 가짜 데이터 시드")
    args = parser.parse_args(argv)

    settings = Settings()
    setup_logging(settings.secret_values())
    market = Market(args.market)
    universe = load_universe(settings.universe_path)

    if args.ignore_hours and settings.broker != "fake":
        print("--ignore-hours는 fake 증권사에서만 쓸 수 있습니다.")
        return 2
    if settings.broker != "fake":
        print("KIS 증권사 연결은 M5에서 추가됩니다. 지금은 BROKER=fake만 쓸 수 있습니다.")
        return 2

    # 가짜 일봉은 어제까지 만든다(오늘 봉은 장 시작 전이라 없음)
    yesterday = (pd.Timestamp(trading_date(market, now_utc())) - pd.offsets.BDay(1)).date()
    broker = make_demo_broker(universe, end=yesterday, seed=args.seed)

    store = build_store(settings)
    engine = Engine(
        broker=broker,
        store=store,
        notifier=build_notifier(settings, store),
        strategies=[create_strategy(name) for name in STRATEGIES],
        universe=universe,
        mode=settings.trading_mode,
        daily_buy_caps={
            Market.KR: settings.max_daily_buy_krw,
            Market.US: settings.max_daily_buy_usd,
        },
        secrets=settings.secret_values(),
        check_market_hours=not args.ignore_hours,
    )
    report = engine.run(market)
    # 오류나 알림 실패는 종료 코드로도 알린다(GitHub Actions 실패 표시)
    return 1 if report.status is RunStatus.ERROR or report.notify_failed else 0


if __name__ == "__main__":
    sys.exit(main())
