"""Anthropic Claude 어댑터. 실제 API는 부르지 않고 SDK 클라이언트 자리에 가짜를 넣는다."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from trading.config import Settings
from trading.engine.factory import build_llm, build_screener
from trading.llm.base import LLMError
from trading.llm.claude import DEFAULT_MODEL, FALLBACK_BETA, ClaudeLLM


class FakeMessages:
    def __init__(self, result):
        self.result = result
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def fake_client(result) -> tuple[SimpleNamespace, FakeMessages]:
    messages = FakeMessages(result)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def message(text="답", stop_reason="end_turn"):
    blocks = [
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text=text),
    ]
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


def llm(result, model=None) -> tuple[ClaudeLLM, FakeMessages]:
    client, messages = fake_client(result)
    return ClaudeLLM("sk-ant-test", model, client=client), messages


def test_request_shape_and_text():
    claude, messages = llm(message("  요약 문장  "))
    assert claude.complete(system="시스템", prompt="질문", max_tokens=2000) == "요약 문장"
    kw = messages.kwargs
    assert kw["model"] == DEFAULT_MODEL == claude.model
    assert kw["system"] == "시스템"
    assert kw["messages"] == [{"role": "user", "content": "질문"}]
    assert kw["max_tokens"] == 2000
    assert kw["output_config"] == {"effort": "low"}
    assert (kw["betas"], kw["fallbacks"]) == ([FALLBACK_BETA], "default")


def test_model_without_server_fallback_sends_no_fallbacks():
    claude, messages = llm(message(), model="claude-haiku-5-5")
    claude.complete(system="s", prompt="p", max_tokens=100)
    assert messages.kwargs["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in messages.kwargs and "betas" not in messages.kwargs


@pytest.mark.parametrize(
    ("result", "match"),
    [
        (message(stop_reason="refusal"), "거절"),
        (message(stop_reason="max_tokens"), "잘렸"),
        (message(text="  "), "비어"),
    ],
)
def test_unusable_answers_raise(result, match):
    claude, _ = llm(result)
    with pytest.raises(LLMError, match=match):
        claude.complete(system="s", prompt="p", max_tokens=100)


REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize(
    ("error", "match"),
    [
        (anthropic.APITimeoutError(request=REQUEST), "시간 초과"),
        (anthropic.APIConnectionError(request=REQUEST), "연결"),
        (
            anthropic.RateLimitError(
                "rate", response=httpx2.Response(429, request=REQUEST), body=None
            ),
            "429",
        ),
        (
            anthropic.InternalServerError(
                "boom", response=httpx2.Response(500, request=REQUEST), body=None
            ),
            "500",
        ),
    ],
)
def test_api_errors_become_llm_errors(error, match):
    claude, _ = llm(error)
    with pytest.raises(LLMError, match=match) as e:
        claude.complete(system="s", prompt="p", max_tokens=100)
    assert "sk-ant-test" not in str(e.value)


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_factory_builds_claude_only_when_configured():
    built = build_llm(
        settings(llm_provider="anthropic", llm_api_key="sk-ant-x", llm_model="claude-haiku-5-5")
    )
    assert isinstance(built, ClaudeLLM) and built.model == "claude-haiku-5-5"
    assert isinstance(build_llm(settings(llm_provider="Anthropic", llm_api_key="k")), ClaudeLLM)
    # 설정이 모자라거나 틀려도 매매는 멈추지 않는다: LLM 기능만 끈다
    assert build_llm(settings(llm_provider="anthropic")) is None
    assert build_llm(settings(llm_provider="openai", llm_api_key="k")) is None
    assert build_screener(settings(llm_screen="shadow"), None) is None
