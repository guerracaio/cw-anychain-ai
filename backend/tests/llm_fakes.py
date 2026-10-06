"""Scripted LLM provider for tests: each step returns the next prepared turn."""

from collections.abc import Callable

from app.llm.base import LLMError, ToolCall, ToolResult, ToolSpec, Turn, Usage


def call(name: str, arguments: dict | None = None, call_id: str | None = None) -> ToolCall:
    return ToolCall(call_id or f"id-{name}", name, arguments or {})


def submit(**fields) -> ToolCall:
    payload = {
        "summary": "Resumo.",
        "findings": [],
        "likely_causes": [],
        "next_steps": [],
        "security_notes": [],
        "uncertainties": [],
        **fields,
    }
    return call("submit_analysis", payload)


class FakeSession:
    def __init__(self, provider: "FakeProvider"):
        self.provider = provider

    async def step(self, results: list[ToolResult], note: str | None = None) -> Turn:
        self.provider.received.append((results, note))
        if not self.provider.script:
            raise LLMError("script_exhausted")
        item = self.provider.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(results)
        calls = item if isinstance(item, list) else [item]
        return Turn(
            tool_calls=[c for c in calls if isinstance(c, ToolCall)],
            text=next((c for c in calls if isinstance(c, str)), None),
            usage=Usage(input_tokens=100, output_tokens=20, thinking_tokens=5),
        )


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, script: list[object | Callable[[list[ToolResult]], object]]):
        self.script = list(script)
        self.received: list[tuple[list[ToolResult], str | None]] = []
        self.system = ""
        self.user = ""
        self.tools: list[ToolSpec] = []

    def start(self, *, system, user, tools, max_output_tokens):
        self.system, self.user, self.tools = system, user, tools
        return FakeSession(self)
