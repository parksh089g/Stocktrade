"""알림 인터페이스. 실제 알림은 웹앱 푸시(webpush.py)로 보낸다."""

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class NotifyError(Exception):
    """알림을 한 곳에도 전하지 못했다."""


class Notification(BaseModel):
    title: str
    body: str
    url: str = "/"  # 알림을 눌렀을 때 웹앱에서 열 주소
    urgent: bool = False  # 오류/확인 필요: 폰이 절전 중이어도 바로 깨운다

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}"


class Notifier(ABC):
    @abstractmethod
    def send(self, notification: Notification) -> None:
        """전하지 못하면 예외를 던진다."""


class ConsoleNotifier(Notifier):
    """화면(GitHub Actions 로그)에 출력한다."""

    def send(self, notification: Notification) -> None:
        print(notification.text)


class MemoryNotifier(Notifier):
    """보낸 내용을 모아 둔다. 테스트용."""

    def __init__(self) -> None:
        self.messages: list[Notification] = []

    def send(self, notification: Notification) -> None:
        self.messages.append(notification)


class MultiNotifier(Notifier):
    """여러 곳에 보낸다. 하나가 실패해도 나머지는 보내고, 마지막에 실패를 알린다."""

    def __init__(self, notifiers: Sequence[Notifier]) -> None:
        self.notifiers = list(notifiers)

    def send(self, notification: Notification) -> None:
        errors = []
        for notifier in self.notifiers:
            try:
                notifier.send(notification)
            except Exception as e:
                logger.exception("%s 알림 실패", type(notifier).__name__)
                errors.append(f"{type(notifier).__name__}: {e}")
        if errors:
            raise NotifyError("; ".join(errors))
