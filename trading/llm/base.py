"""LLM 공통 인터페이스. 회사별 어댑터는 이 인터페이스를 구현한다.

테스트와 로컬은 FakeLLM을 쓴다. 호출 결과는 문자열 하나다(대화나 도구 호출은 쓰지 않는다).
"""

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class LLMError(Exception):
    """LLM 호출 실패(연결, 시간 초과, 거부, 빈 응답 등)."""


class LLMClient(ABC):
    # 기록에 남길 모델 이름(예: claude-haiku-5-5)
    model: str = ""

    @abstractmethod
    def complete(self, *, system: str, prompt: str, max_tokens: int) -> str:
        """system 지시와 prompt로 답 문장 하나를 받는다. 실패하면 LLMError."""


@dataclass
class LLMCall:
    system: str
    prompt: str
    max_tokens: int


class FakeLLM(LLMClient):
    """정해 둔 답을 돌려주는 가짜 LLM. 받은 요청을 기록한다.

    reply는 문자열, 예외, 또는 prompt를 받아 그 둘 중 하나를 돌려주는 함수다.
    """

    model = "fake"

    def __init__(self, reply: str | Exception | Callable[[str], str | Exception] = "") -> None:
        self.reply = reply
        self.calls: list[LLMCall] = []

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> str:
        self.calls.append(LLMCall(system, prompt, max_tokens))
        reply = self.reply(prompt) if callable(self.reply) else self.reply
        if isinstance(reply, Exception):
            raise reply
        return reply


_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_object(text: str) -> dict[str, Any]:
    """답에서 JSON 객체 하나를 꺼낸다(앞뒤 설명이나 ``` 묶음이 붙어 와도). 없으면 ValueError."""
    match = _OBJECT.search(text)
    if not match:
        raise ValueError("JSON 객체가 없습니다")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("JSON 객체가 아닙니다")
    return value
