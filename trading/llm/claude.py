"""Anthropic Claude 어댑터. 공식 SDK(anthropic)로 Messages API를 부른다.

- 짧은 요약과 분류라 effort는 low로 둔다(생각 토큰도 max_tokens 안에 든다).
- 지원하는 모델은 안전 분류기가 거절하면 서버가 다른 모델로 다시 돌린다(fallbacks="default").
- 연결 오류, 시간 초과, 거절, 잘린 답은 모두 LLMError로 바꾼다.
  오류 문장에 API 키는 들어가지 않는다.
"""

import logging

import anthropic

from trading.llm.base import LLMClient, LLMError

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# 서버 쪽 거절 대비(fallbacks="default")를 받는 모델
FALLBACK_MODELS = {
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
    "claude-fable-5-1",
    "claude-fable-5-2",
}


class ClaudeLLM(LLMClient):
    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        *,
        effort: str = "low",
        timeout: float = 45,
        max_retries: int = 1,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self.effort = effort
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries
        )

    def complete(self, *, system: str, prompt: str, max_tokens: int) -> str:
        extra = (
            {"betas": [FALLBACK_BETA], "fallbacks": "default"}
            if self.model in FALLBACK_MODELS
            else {}
        )
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": self.effort},
                **extra,
            )
        except anthropic.APITimeoutError:
            raise LLMError("Claude 응답 시간 초과") from None
        except anthropic.APIConnectionError:
            raise LLMError("Claude 연결 실패") from None
        except anthropic.RateLimitError:
            raise LLMError("Claude 호출 한도 초과(429)") from None
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API 오류({e.status_code})") from None

        if response.stop_reason == "refusal":
            raise LLMError("Claude가 답을 거절했습니다")
        if response.stop_reason == "max_tokens":
            raise LLMError("Claude 답이 길이 상한에서 잘렸습니다")
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise LLMError("Claude 답이 비어 있습니다")
        return text
