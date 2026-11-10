"""웹 푸시 알림 확인: 웹앱에서 알림을 켠 기기로 시험 알림을 보낸다.

  uv run python -m trading.notify.check

.env의 SUPABASE_URL, SUPABASE_SERVICE_KEY(구독 목록), VAPID_PRIVATE_KEY, VAPID_SUBJECT를 쓴다.
실행 기록은 남기지 않는다. 알림을 누르면 웹앱 홈이 열린다.
"""

import sys
from collections.abc import Callable
from typing import Any

from pywebpush import webpush

from trading.config import Settings
from trading.core.log import setup_logging
from trading.notify.base import Notification, NotifyError
from trading.notify.webpush import WebPushNotifier
from trading.storage.base import StoreError
from trading.storage.supabase import SupabaseStore

DEFAULT_SUBJECT = Settings.model_fields["vapid_subject"].default

TEST_NOTIFICATION = Notification(
    title="[시험] 푸시 알림 확인",
    body="이 알림이 보이면 폰 알림이 됩니다. 누르면 웹앱이 열립니다.",
    url="/",
)


def main(*, send: Callable[..., Any] = webpush) -> int:
    settings = Settings()
    setup_logging(settings.secret_values())
    required = {
        "SUPABASE_URL": settings.supabase_url,
        "SUPABASE_SERVICE_KEY": settings.supabase_service_key,
        "VAPID_PRIVATE_KEY": settings.vapid_private_key,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        print(f".env에 {', '.join(missing)}를 넣어 주세요(docs/SETUP.md 6장).")
        return 2
    assert settings.supabase_url and settings.supabase_service_key and settings.vapid_private_key
    if settings.vapid_subject == DEFAULT_SUBJECT:
        print("참고: VAPID_SUBJECT가 기본값입니다. .env에 mailto:본인메일주소 를 넣어 두세요.")

    store = SupabaseStore(
        settings.supabase_url,
        settings.supabase_service_key.get_secret_value(),
        settings.trading_mode,
    )
    notifier = WebPushNotifier(
        store, settings.vapid_private_key.get_secret_value(), settings.vapid_subject, send=send
    )
    try:
        print(f"알림을 켠 기기: {len(store.get_push_subscriptions())}대")
        notifier.send(TEST_NOTIFICATION)
    except (StoreError, NotifyError) as e:
        print(f"실패: {e}")
        return 1
    print("시험 알림을 보냈습니다. 폰에 알림이 왔는지 확인하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
