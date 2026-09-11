"""A scripted, deterministic stand-in for a real model call.

Used by tests/test_agent_loop.py so the loop's control flow (validation,
retries, ceilings) can be tested without network access or an API key —
the loop only ever depends on the ChatFn protocol, never on the anthropic
package directly.
"""

from opspilot.agent.client import ModelResponse, TextBlock, ToolUseBlock


class ScriptedChatFn:
    def __init__(
        self,
        responses: list[ModelResponse],
        default: ModelResponse | None = None,
    ):
        self._responses = list(responses)
        self._default = default
        self.calls: list[dict] = []

    def __call__(self, *, messages: list[dict], tools: list[dict], system: str) -> ModelResponse:
        self.calls.append({"messages": messages, "tools": tools, "system": system})
        if self._responses:
            return self._responses.pop(0)
        if self._default is not None:
            return self._default
        raise AssertionError("ScriptedChatFn exhausted with no default response configured")


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
