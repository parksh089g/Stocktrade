"""KIS 연결 확인. 기본은 조회만 하고 주문은 내지 않는다.

  uv run python -m trading.brokers.kis_check                 # 국내/미국 잔고, 보유, 시세, 일봉 등
  uv run python -m trading.brokers.kis_check --market KR
  uv run python -m trading.brokers.kis_check --order-test KR  # 모의투자 전용 1주 매수 시험

주문 시험은 체결되지 않을 가격(현재가 -20%)으로 1주 매수 주문을 낸다.

주문 시험은 장중(국내 09:00~15:30, 미국 정규장)에만 접수된다. 시험한 종목은 같은 날 같은 종목
중복 주문 방지 규칙 때문에 그날 엔진이 주문하지 않는다.
"""

import argparse
import sys
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from trading.brokers.kis import KisBroker
from trading.config import Settings
from trading.core.clock import format_kst, now_utc, trading_date
from trading.core.log import setup_logging
from trading.core.models import Market, Order, OrderType, Side
from trading.core.ticks import floor_to_tick
from trading.engine.factory import SetupError, build_kis_broker
from trading.universe import load_universe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m trading.brokers.kis_check")
    parser.add_argument("--market", choices=[m.value for m in Market])
    parser.add_argument("--order-test", choices=[m.value for m in Market])
    args = parser.parse_args(argv)

    settings = Settings()
    setup_logging(settings.secret_values())
    universe = load_universe(settings.universe_path)
    try:
        broker = build_kis_broker(settings, universe)
    except SetupError as e:
        print(f"설정 오류: {e}")
        return 2
    if args.order_test and settings.is_live:
        print("주문 시험은 모의투자에서만 할 수 있습니다.")
        return 2

    print(f"KIS {'실전' if settings.is_live else '모의투자'} 연결 확인 · {format_kst(now_utc())}")
    markets = [Market(args.market)] if args.market else list(Market)
    ok = True
    for market in markets:
        symbol = universe[market][0].symbol if universe.get(market) else None
        ok &= _check_market(broker, market, symbol)
    if args.order_test:
        market = Market(args.order_test)
        ok &= _order_test(broker, market, universe[market][0].symbol)
    print("\n완료" if ok else "\n일부 확인 실패 (위 메시지 참고)")
    return 0 if ok else 1


def _check_market(broker: KisBroker, market: Market, symbol: str | None) -> bool:
    print(f"\n[{market.label}]")
    fmt = market.format_price
    today = trading_date(market, now_utc())

    def balance() -> str:
        b = broker.get_balance(market)
        return f"주문 가능 {fmt(b.cash)} / 총평가 {fmt(b.total_equity)} {market.currency}"

    def holdings() -> str:
        items = broker.get_holdings(market)
        shown = ", ".join(f"{h.symbol} {h.qty}주@{fmt(h.avg_price)}" for h in items[:10])
        return f"{len(items)}종목" + (f" ({shown})" if shown else "")

    def price() -> str:
        return f"{symbol} {fmt(broker.get_price(market, symbol))}"

    def bars() -> str:
        df = broker.get_daily_bars(market, symbol, 99)
        last = df.iloc[-1]
        return f"{symbol} {len(df)}개, 마지막 {df.index[-1].date()} 종가 {fmt(last['close'])}"

    def orders() -> str:
        found = broker.get_orders(market, today)
        return f"{today} {len(found)}건"

    steps: list[tuple[str, Callable[[], Any]]] = [
        ("잔고", balance),
        ("보유", holdings),
        ("휴장일 여부", lambda: "개장일" if broker.is_trading_day(market, today) else "휴장일"),
        ("오늘 주문", orders),
    ]
    if symbol:
        steps[2:2] = [("현재가", price), ("일봉", bars)]
    return all(_step(name, fn) for name, fn in steps)


def _order_test(broker: KisBroker, market: Market, symbol: str) -> bool:
    print(f"\n[주문 시험: {market.label} {symbol}]")
    price = broker.get_price(market, symbol)
    limit = floor_to_tick(market, price * Decimal("0.8"))
    order = Order(
        market=market,
        symbol=symbol,
        side=Side.BUY,
        qty=1,
        order_type=OrderType.LIMIT,
        limit_price=limit,
        strategy="check",
        client_order_id=f"check-{market}-{symbol}",
    )
    result = broker.place_order(order)
    print(f"  주문: 1주 지정가 {market.format_price(limit)} → {result.status} {result.message}")
    if result.broker_order_id is None:
        return False
    today = trading_date(market, now_utc())
    found = [
        r for r in broker.get_orders(market, today) if r.broker_order_id == result.broker_order_id
    ]
    if not found:
        print("  주문 내역에서 방금 주문을 찾지 못했습니다")
        return False
    r = found[0]
    print(f"  주문 내역: {r.status}, 체결 {r.filled_qty}/{r.order.qty}주")
    return True


def _step(name: str, fn: Callable[[], Any]) -> bool:
    try:
        print(f"  {name}: {fn()}")
        return True
    except Exception as e:
        print(f"  {name}: 실패 - {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    sys.exit(main())
