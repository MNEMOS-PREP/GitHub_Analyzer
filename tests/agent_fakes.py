"""Shared test doubles for the PR agent: a scripted chat client and a tool-call builder."""

from __future__ import annotations

import copy
import json

from gh_analyzer.agents.llm import LLMError, Usage

DEFAULT_USAGE = Usage(100, 50)


def tool_call(name: str, args, call_id: str = "c1", content: str | None = None) -> dict:
    """Assistant message with one tool call. `args` may be a dict or a raw (even malformed) string."""
    raw = args if isinstance(args, str) else json.dumps(args)
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": raw},
        }],
    }


class FakeClient:
    """ChatClient that replays a script.

    Each step is an assistant message dict, a `(message, Usage)` tuple, an `LLMError`
    instance (raised), or a callable `messages -> message | (message, Usage)`.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def chat(self, messages, tools=None, temperature=0.2, max_tokens=1500, tool_choice=None):
        self.calls.append({
            "messages": copy.deepcopy(messages),
            "tools": copy.deepcopy(tools),
            "tool_choice": copy.deepcopy(tool_choice),
            "max_tokens": max_tokens,
        })
        if len(self.calls) > len(self.script):
            raise AssertionError("FakeClient exhausted")
        step = self.script[len(self.calls) - 1]
        if callable(step) and not isinstance(step, LLMError):
            step = step(copy.deepcopy(messages))
        if isinstance(step, LLMError):
            raise step
        if isinstance(step, tuple):
            message, usage = step
            return message, usage
        return step, Usage(DEFAULT_USAGE.prompt_tokens, DEFAULT_USAGE.completion_tokens)
