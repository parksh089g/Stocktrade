"""알림 인터페이스. M4에서 텔레그램으로 구현한다."""

from abc import ABC, abstractmethod


class Notifier(ABC):
    @abstractmethod
    def send(self, text: str) -> None: ...


class ConsoleNotifier(Notifier):
    """화면에 출력한다. 로컬 실행용."""

    def send(self, text: str) -> None:
        print(text)


class MemoryNotifier(Notifier):
    """보낸 내용을 모아 둔다. 테스트용."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, text: str) -> None:
        self.messages.append(text)
