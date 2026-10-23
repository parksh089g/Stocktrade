"""웹앱(PWA) 푸시 알림 발송.

웹앱이 저장소(push_subscriptions)에 등록한 기기마다 VAPID 서명으로 푸시를 보낸다.
만료된 구독(404/410)은 지운다. 한 기기에도 전하지 못하면 NotifyError를 던져
실행이 실패로 끝나게 한다(GitHub Actions 실패 메일이 백업 알림이 된다).
"""

import json
import logging
from collections.abc import Callable
from typing import Any

from pywebpush import WebPushException, webpush

from trading.notify.base import Notification, Notifier, NotifyError
from trading.storage.base import Store

logger = logging.getLogger(__name__)

# 푸시 내용은 암호화 후 4KB까지라 본문을 이 바이트 수 안으로 줄인다(한글 1자 = 3바이트)
MAX_BODY_BYTES = 2_800
# 폰이 꺼져 있으면 이 시간(초) 동안 보관했다가 전달한다
TTL_SECONDS = 24 * 60 * 60
_GONE = (404, 410)


class WebPushNotifier(Notifier):
    def __init__(
        self,
        store: Store,
        vapid_private_key: str,
        vapid_subject: str,
        *,
        send: Callable[..., Any] = webpush,
    ) -> None:
        self.store = store
        self._private_key = vapid_private_key
        self._subject = vapid_subject
        self._send = send

    def send(self, notification: Notification) -> None:
        subscriptions = self.store.get_push_subscriptions()
        if not subscriptions:
            raise NotifyError("푸시를 받을 기기가 없습니다. 웹앱에서 알림을 켜 주세요")

        payload = json.dumps(
            {
                "title": notification.title,
                "body": truncate_utf8(notification.body, MAX_BODY_BYTES),
                "url": notification.url,
                "urgent": notification.urgent,
            },
            ensure_ascii=False,
        )
        delivered, problems = 0, []
        for sub in subscriptions:
            try:
                self._send(
                    subscription_info={
                        "endpoint": sub.endpoint,
                        "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                    },
                    data=payload,
                    vapid_private_key=self._private_key,
                    # pywebpush가 이 dict에 aud/exp를 채우므로 기기마다 새로 만든다
                    vapid_claims={"sub": self._subject},
                    ttl=TTL_SECONDS,
                    headers={"Urgency": "high" if notification.urgent else "normal"},
                    timeout=10,
                )
                delivered += 1
            except WebPushException as e:
                status = getattr(e.response, "status_code", None)
                if status in _GONE:
                    logger.info("만료된 푸시 구독을 지웁니다")
                    self.store.delete_push_subscription(sub.endpoint)
                    problems.append("만료된 구독 삭제")
                else:
                    logger.warning("푸시 실패: %s", status)
                    problems.append(f"응답 {status}")
            except Exception as e:
                logger.warning("푸시 실패: %s", type(e).__name__)
                problems.append(type(e).__name__)

        if delivered == 0:
            raise NotifyError(f"푸시를 한 기기에도 보내지 못했습니다({', '.join(problems)})")


def truncate_utf8(text: str, max_bytes: int) -> str:
    data = text.encode()
    if len(data) <= max_bytes:
        return text
    suffix = "\n…(웹앱에서 전체 보기)"
    cut = data[: max_bytes - len(suffix.encode())]
    return cut.decode(errors="ignore") + suffix
