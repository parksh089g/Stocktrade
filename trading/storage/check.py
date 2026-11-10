"""Supabase 연결 확인.

  uv run python -m trading.storage.check

.env의 SUPABASE_URL, SUPABASE_SERVICE_KEY로 접속해 표가 다 있는지, 읽고 쓸 수 있는지 본다.
쓰기 확인용 보유분과 엔진 캐시 한 줄씩을 넣었다가 바로 지운다(이름 __CHECK__).
"""

import sys
from datetime import UTC, datetime
from decimal import Decimal

from trading.config import Settings
from trading.core.log import setup_logging
from trading.core.models import Market, Position
from trading.storage.base import StoreError
from trading.storage.supabase import SupabaseStore

CHECK_SYMBOL = "__CHECK__"
CHECK_STRATEGY = "__check__"


def main() -> int:
    settings = Settings()
    setup_logging(settings.secret_values())
    if not settings.supabase_url or not settings.supabase_service_key:
        print(".env에 SUPABASE_URL과 SUPABASE_SERVICE_KEY를 넣어 주세요.")
        return 2
    store = SupabaseStore(
        settings.supabase_url,
        settings.supabase_service_key.get_secret_value(),
        settings.trading_mode,
    )
    try:
        pause = store.get_pause()
        print(f"일시정지 스위치: {'켜짐' if pause.paused else '꺼짐'}")
        for market in Market:
            positions = store.get_positions(market)
            open_orders = store.get_open_orders(market)
            print(f"{market.label}: 보유 {len(positions)}건, 미체결 주문 {len(open_orders)}건")
        # 나중에 생긴 칸. 엔진이 주문을 기록할 때 쓰므로 없으면 주문 기록이 실패한다
        store.check_columns("orders", ["reason"])
        print("주문 이유 칸(reason) 확인")
        print(f"푸시 받을 기기: {len(store.get_push_subscriptions())}대")

        store.upsert_position(
            Position(
                market=Market.KR,
                symbol=CHECK_SYMBOL,
                strategy=CHECK_STRATEGY,
                qty=1,
                avg_price=Decimal(1),
                opened_at=datetime.now(UTC),
            )
        )
        store.delete_position(Market.KR, CHECK_SYMBOL, CHECK_STRATEGY)
        store.put_cache(CHECK_SYMBOL, "ok")
        if store.get_cache(CHECK_SYMBOL) != "ok":
            raise StoreError("엔진 캐시(engine_cache)에 쓴 값을 다시 읽지 못했습니다")
        store.delete_cache(CHECK_SYMBOL)
        print("쓰기/지우기 확인")
    except StoreError as e:
        print(f"실패: {e}")
        return 1
    print(f"Supabase 연결 확인 완료 (모드: {settings.trading_mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
