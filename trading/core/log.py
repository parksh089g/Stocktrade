"""로깅 설정. 비밀값이 로그에 찍히지 않도록 출력 직전에 가린다."""

import logging
import sys
from collections.abc import Iterable

MASK = "***"
# 너무 짧은 값은 일반 문자열까지 가릴 수 있어 제외한다
_MIN_SECRET_LEN = 4


def redact(text: str, secrets: Iterable[str]) -> str:
    """text 안의 비밀값을 가린다. 알림이나 저장할 오류 문장에도 쓴다."""
    # 긴 값부터 바꿔야 겹치는 비밀값이 일부만 남지 않는다
    for secret in sorted({s for s in secrets if len(s) >= _MIN_SECRET_LEN}, key=len, reverse=True):
        text = text.replace(secret, MASK)
    return text


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, secrets: Iterable[str]) -> None:
        super().__init__(fmt)
        self._secrets = list(secrets)

    def format(self, record: logging.LogRecord) -> str:
        # 예외 traceback까지 포함된 최종 문자열에서 가린다
        return redact(super().format(record), self._secrets)


def setup_logging(secrets: Iterable[str] = (), level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s", secrets)
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx는 INFO에서 요청 URL을 찍으므로 경고 이상만 남긴다
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
