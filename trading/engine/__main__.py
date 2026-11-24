"""하루 실행.

예)
  uv run python -m trading.engine --market KR
  uv run python -m trading.engine --market KR --dry-run        # 계산만 하고 주문은 내지 않는다
  uv run python -m trading.engine --market US --ignore-hours   # fake 증권사로 장 시간 무시하고 확인
  uv run python -m trading.engine --market KR --stop-loss-only # 장 마감 전 손절만 확인

fake 증권사와 시험 실행은 메모리 저장소와 화면 출력만 쓴다.
KIS는 Supabase 저장소와 웹 푸시(VAPID 키가 있으면)를 쓴다.
"""

import argparse
import sys

from trading.config import Settings
from trading.core.log import setup_logging
from trading.core.models import Market
from trading.engine.factory import (
    SetupError,
    build_broker,
    build_llm,
    build_notifier,
    build_screener,
    build_store,
)
from trading.engine.report import RunStatus
from trading.engine.runner import Engine
from trading.strategies import STRATEGIES, create_strategy
from trading.universe import BENCHMARKS, load_universe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m trading.engine")
    parser.add_argument("--market", required=True, choices=[m.value for m in Market])
    parser.add_argument(
        "--dry-run", action="store_true", help="시세/잔고로 계산만 하고 주문은 내지 않는다"
    )
    parser.add_argument(
        "--ignore-hours",
        action="store_true",
        help="fake 증권사나 시험 실행(--dry-run)에서만: 휴장일/주문 시간 검사를 건너뛴다",
    )
    parser.add_argument(
        "--stop-loss-only",
        action="store_true",
        help="장 마감 전 손절 확인: 보유 종목을 지금 가격으로 보고 손절 매도만 낸다(매수 안 함)",
    )
    parser.add_argument(
        "--once-per-day",
        action="store_true",
        help="예약 실행용: 오늘 같은 종류의 실행이 이미 끝났으면 기록과 알림 없이 끝낸다",
    )
    parser.add_argument("--seed", type=int, default=0, help="fake 증권사 가짜 데이터 시드")
    args = parser.parse_args(argv)

    settings = Settings()
    setup_logging(settings.secret_values())
    market = Market(args.market)

    if args.ignore_hours and settings.broker != "fake" and not args.dry_run:
        print("--ignore-hours는 fake 증권사나 시험 실행(--dry-run)에서만 쓸 수 있습니다.")
        return 2
    try:
        universe = load_universe(settings.universe_path)
        broker = build_broker(settings, universe, market, seed=args.seed)
        store = build_store(settings, dry_run=args.dry_run)
        notifier = build_notifier(settings, store)
        llm = build_llm(settings)
        screener = build_screener(settings, llm)
    except SetupError as e:
        print(f"설정 오류: {e}")
        return 2

    engine = Engine(
        broker=broker,
        store=store,
        notifier=notifier,
        strategies=[create_strategy(name) for name in STRATEGIES],
        universe=universe,
        mode=settings.trading_mode,
        daily_buy_caps={
            Market.KR: settings.max_daily_buy_krw,
            Market.US: settings.max_daily_buy_usd,
        },
        secrets=settings.secret_values(),
        check_market_hours=not args.ignore_hours,
        dry_run=args.dry_run,
        stop_loss_only=args.stop_loss_only,
        once_per_day=args.once_per_day,
        benchmarks=BENCHMARKS,
        llm=llm if settings.llm_summary else None,
        screener=screener,
    )
    report = engine.run(market)
    # 오류나 알림 실패는 종료 코드로도 알린다(GitHub Actions 실패 표시)
    return 1 if report.status is RunStatus.ERROR or report.notify_failed else 0


if __name__ == "__main__":
    sys.exit(main())
