"""OpenAI adapter (Responses API, openai SDK). All SDK types stay inside this module."""

import json
from typing import Any

import openai
from openai import AsyncOpenAI

from app.llm.base import LLMError, ToolCall, ToolResult, ToolSpec, Turn, Usage


def error_code(exc: Exception) -> str:
    if isinstance(exc, openai.APITimeoutError):
        return "llm_timeout"
    if isinstance(exc, openai.APIConnectionError):
        return "llm_connection_failed"
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        if status in (401, 403):
            return "llm_access_denied"
        if status == 404:
            return "llm_model_not_found"
        if status == 429:
            # A 429 is also returned when the account has no credit left.
            return "llm_quota_exceeded" if exc.code == "insufficient_quota" else "llm_rate_limited"
        if status >= 500:
            return "llm_unavailable"
        return "llm_bad_request"
    return "llm_connection_failed"


def to_tool(spec: ToolSpec) -> dict[str, Any]:
    # Non-strict: the shared schemas use optional fields that strict mode would reject.
    return {
        "type": "function",
        "name": spec.name,
        "description": spec.description,
        "parameters": spec.parameters,
        "strict": False,
    }


def usage_of(response: Any) -> Usage:
    usage = response.usage
    if usage is None:
        return Usage()
    details = usage.output_tokens_details
    reasoning = (details.reasoning_tokens if details else 0) or 0
    # OpenAI counts reasoning inside output_tokens; Usage keeps them apart (both billed).
    return Usage(
        input_tokens=usage.input_tokens or 0,
        output_tokens=max((usage.output_tokens or 0) - reasoning, 0),
        thinking_tokens=reasoning,
    )


class OpenAISession:
    def __init__(
        self,
        provider: "OpenAIProvider",
        system: str,
        user: str,
        tools: list[ToolSpec],
        max_output_tokens: int,
    ):
        self.provider = provider
        self.system = system
        self.tools = [to_tool(t) for t in tools]
        self.max_output_tokens = max_output_tokens
        self.history: list[dict[str, Any]] = [{"role": "user", "content": user}]
        self.started = False

    def _append_results(self, results: list[ToolResult], note: str | None) -> None:
        for result in results:
            self.history.append(
                {
                    "type": "function_call_output",
                    "call_id": result.call.id,
                    "output": json.dumps(result.content, ensure_ascii=False, default=str),
                }
            )
        if note:
            self.history.append({"role": "user", "content": note})

    def _request(self) -> dict[str, Any]:
        provider = self.provider
        request: dict[str, Any] = {
            "model": provider.model,
            "instructions": self.system,
            "input": self.history,
            "tools": self.tools,
            # The harness owns the loop: every turn must be a tool call, including the final
            # submit tool, which keeps outputs structured.
            "tool_choice": "required",
            "parallel_tool_calls": True,
            "max_output_tokens": self.max_output_tokens,
            # Stateless: the session resends its own history; nothing is stored server-side.
            "store": False,
            # Same system prompt and tools on every analysis: lets the provider reuse the prefix.
            "prompt_cache_key": "anychain-agent",
        }
        if provider.reasoning_effort:
            request["reasoning"] = {"effort": provider.reasoning_effort}
            # Reasoning items must be sent back on stateless calls; only the encrypted form is.
            request["include"] = ["reasoning.encrypted_content"]
        if provider.temperature is not None:
            request["temperature"] = provider.temperature
        return request

    async def step(self, results: list[ToolResult], note: str | None = None) -> Turn:
        if self.started:
            self._append_results(results, note)
        self.started = True
        try:
            response = await self.provider.client.responses.create(**self._request())
        except Exception as exc:  # SDK and transport errors are mapped to safe codes.
            raise LLMError(error_code(exc)) from None
        output = list(response.output or [])
        if not output:
            raise LLMError("llm_empty_response")
        # Keep the model turn verbatim (reasoning items included) for the next request.
        self.history.extend(item.model_dump(mode="json", exclude_none=True) for item in output)
        turn = Turn(usage=usage_of(response), finish_reason=response.status)
        texts = []
        for item in output:
            if item.type == "function_call":
                try:
                    arguments = json.loads(item.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {"_invalid_json": True}
                turn.tool_calls.append(
                    ToolCall(
                        id=item.call_id,
                        name=item.name,
                        arguments=arguments if isinstance(arguments, dict) else {},
                    )
                )
            elif item.type == "message":
                texts.extend(part.text for part in item.content or [] if part.type == "output_text")
        turn.text = "\n".join(texts) or None
        return turn


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        temperature: float | None = None,
        client: Any = None,
        reasoning_effort: str | None = None,
    ):
        self.model = model
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        # The SDK retries 408/409/429/5xx and connection errors with backoff.
        self.client = client or AsyncOpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=3)

    def start(
        self, *, system: str, user: str, tools: list[ToolSpec], max_output_tokens: int
    ) -> OpenAISession:
        return OpenAISession(self, system, user, tools, max_output_tokens)
