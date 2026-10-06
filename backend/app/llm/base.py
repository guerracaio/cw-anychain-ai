"""Provider-neutral LLM interface. Nothing outside app/llm imports a provider SDK."""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    # JSON Schema (object) describing the arguments.
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call: ToolCall
    content: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    # Reasoning tokens are billed as output by current providers; tracked separately.
    thinking_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.thinking_tokens += other.thinking_tokens


@dataclass
class Turn:
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str | None = None
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None


class LLMError(Exception):
    """Safe, application-owned error code; provider messages never cross this boundary."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class LLMSession(Protocol):
    async def step(self, results: list[ToolResult], note: str | None = None) -> Turn:
        """Send tool results (and an optional harness note) and return the next model turn.

        The first call sends only the initial context. Sessions keep the conversation in the
        provider's native format, so provider-specific data (e.g. thought signatures) survives.
        """
        ...


class LLMProvider(Protocol):
    name: str
    model: str

    def start(
        self, *, system: str, user: str, tools: list[ToolSpec], max_output_tokens: int
    ) -> LLMSession: ...
