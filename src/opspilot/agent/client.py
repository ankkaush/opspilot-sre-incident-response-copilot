"""A thin, provider-agnostic wrapper around the raw model call.

The loop in opspilot.agent.loop never imports the `anthropic` package
directly — it depends only on `ChatFn`, `ModelResponse`, `TextBlock`, and
`ToolUseBlock` defined here. That's what makes tests/test_agent_loop.py able
to script a fake model deterministically, with no network access and no API
key, while `AnthropicChatClient` below is the one real implementation.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import anthropic

log = logging.getLogger("opspilot.agent")


@dataclass(frozen=True)
class TextBlock:
    text: str


@dataclass(frozen=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict


ContentBlock = TextBlock | ToolUseBlock


@dataclass(frozen=True)
class ModelResponse:
    content: list[ContentBlock]
    stop_reason: str
    input_tokens: int
    output_tokens: int


class ChatFn(Protocol):
    def __call__(self, *, messages: list[dict], tools: list[dict], system: str) -> ModelResponse: ...


class TransientProviderError(Exception):
    """Raised for retryable provider failures (timeouts, rate limits, 5xx)."""


# Approximate, for a deterministic per-run cost ceiling only — not a billing
# source of truth. USD per million tokens.
_PRICING_PER_MTOK = {
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
    "claude-opus-5": {"input": 15.0, "output": 75.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
}
_DEFAULT_PRICING = {"input": 3.0, "output": 15.0}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = _PRICING_PER_MTOK.get(model, _DEFAULT_PRICING)
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


class AnthropicChatClient:
    def __init__(self, api_key: str, model: str, max_tokens: int = 2048):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def __call__(self, *, messages: list[dict], tools: list[dict], system: str) -> ModelResponse:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                tools=tools,
                messages=messages,
            )
        except (anthropic.APITimeoutError, anthropic.RateLimitError, anthropic.APIConnectionError) as exc:
            raise TransientProviderError(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise TransientProviderError(str(exc)) from exc
            raise

        blocks: list[ContentBlock] = []
        for block in response.content:
            if block.type == "text":
                blocks.append(TextBlock(text=block.text))
            elif block.type == "tool_use":
                blocks.append(ToolUseBlock(id=block.id, name=block.name, input=dict(block.input)))

        return ModelResponse(
            content=blocks,
            stop_reason=response.stop_reason or "end_turn",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


def call_with_retries(
    chat_fn: ChatFn,
    *,
    messages: list[dict],
    tools: list[dict],
    system: str,
    max_retries: int = 2,
    backoff_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> ModelResponse:
    """Bounded retry with linear backoff — transient provider failures only.
    Anything else (a 4xx, a bug) propagates immediately rather than being
    silently retried."""
    attempt = 0
    while True:
        try:
            return chat_fn(messages=messages, tools=tools, system=system)
        except TransientProviderError as exc:
            attempt += 1
            if attempt > max_retries:
                log.error(
                    "model call failed after retries",
                    extra={"extra_fields": {"attempts": attempt, "error": str(exc)}},
                )
                raise
            log.warning(
                "transient model call failure, retrying",
                extra={"extra_fields": {"attempt": attempt, "error": str(exc)}},
            )
            sleep(backoff_seconds * attempt)
