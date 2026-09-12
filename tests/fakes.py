"""A scripted, deterministic stand-in for a real model call.

Used by tests/test_agent_loop.py so the loop's control flow (validation,
retries, ceilings) can be tested without network access or an API key —
the loop only ever depends on the ChatFn protocol, never on the anthropic
package directly.
"""

from opspilot.agent.client import ModelResponse, TextBlock, ToolUseBlock, TransientProviderError


class ScriptedChatFn:
    """`responses` may mix `ModelResponse` items with `TransientProviderError`
    instances — an error item is raised (not returned), so the same script
    can exercise call_with_retries' bounded-retry-then-give-up path."""

    def __init__(
        self,
        responses: list[ModelResponse | TransientProviderError],
        default: ModelResponse | TransientProviderError | None = None,
    ):
        self._responses = list(responses)
        self._default = default
        self.calls: list[dict] = []

    def __call__(self, *, messages: list[dict], tools: list[dict], system: str) -> ModelResponse:
        self.calls.append({"messages": messages, "tools": tools, "system": system})
        item = self._responses.pop(0) if self._responses else self._default
        if item is None:
            raise AssertionError("ScriptedChatFn exhausted with no default response configured")
        if isinstance(item, TransientProviderError):
            raise item
        return item


def tool_use_response(tool_id: str, name: str, input: dict, *, in_tok=100, out_tok=50) -> ModelResponse:
    return ModelResponse(
        content=[ToolUseBlock(id=tool_id, name=name, input=input)],
        stop_reason="tool_use",
        input_tokens=in_tok,
        output_tokens=out_tok,
    )


def text_response(text: str, *, in_tok=10, out_tok=10) -> ModelResponse:
    return ModelResponse(
        content=[TextBlock(text=text)],
        stop_reason="end_turn",
        input_tokens=in_tok,
        output_tokens=out_tok,
    )
