"""Google Gemini adapter (google-genai SDK). All SDK types stay inside this module."""

from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from app.llm.base import LLMError, ToolCall, ToolResult, ToolSpec, Turn, Usage

RETRY_STATUS = [429, 500, 502, 503, 504]


def error_code(exc: Exception) -> str:
    if isinstance(exc, errors.APIError):
        status = getattr(exc, "code", None)
        if status in (401, 403):
            return "llm_access_denied"
        if status == 404:
            return "llm_model_not_found"
        if status == 429:
            return "llm_rate_limited"
        if isinstance(status, int) and status >= 500:
            return "llm_unavailable"
        return "llm_bad_request"
    if isinstance(exc, httpx.TimeoutException):
        return "llm_timeout"
    return "llm_connection_failed"


def to_declaration(spec: ToolSpec) -> types.FunctionDeclaration:
    return types.FunctionDeclaration(
        name=spec.name, description=spec.description, parameters_json_schema=spec.parameters
    )


def usage_of(response: types.GenerateContentResponse) -> Usage:
    meta = response.usage_metadata
    if meta is None:
        return Usage()
    return Usage(
        input_tokens=meta.prompt_token_count or 0,
        output_tokens=meta.candidates_token_count or 0,
        thinking_tokens=meta.thoughts_token_count or 0,
    )


class GeminiSession:
    def __init__(
        self,
        provider: "GeminiProvider",
        system: str,
        user: str,
        tools: list[ToolSpec],
        max_output_tokens: int,
    ):
        self.provider = provider
        self.history: list[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=user)])
        ]
        self.native_ids: set[str] = set()
        self.started = False
        self.config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[to_declaration(t) for t in tools])],
            # The harness owns the loop: no SDK auto-execution, and every turn must be a tool
            # call (including the final submit tool), which keeps outputs structured.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY
                )
            ),
            max_output_tokens=max_output_tokens,
            temperature=provider.temperature,
            thinking_config=(
                types.ThinkingConfig(thinking_level=provider.thinking_level)
                if provider.thinking_level
                else None
            ),
        )

    def _append_results(self, results: list[ToolResult], note: str | None) -> None:
        parts = [
            types.Part(
                function_response=types.FunctionResponse(
                    id=result.call.id if result.call.id in self.native_ids else None,
                    name=result.call.name,
                    response=result.content,
                )
            )
            for result in results
        ]
        if note:
            parts.append(types.Part(text=note))
        if parts:
            self.history.append(types.Content(role="user", parts=parts))

    async def step(self, results: list[ToolResult], note: str | None = None) -> Turn:
        if self.started:
            self._append_results(results, note)
        self.started = True
        try:
            response = await self.provider.client.aio.models.generate_content(
                model=self.provider.model, contents=self.history, config=self.config
            )
        except Exception as exc:  # SDK and transport errors are mapped to safe codes.
            raise LLMError(error_code(exc)) from None
        candidates = response.candidates or []
        if not candidates or candidates[0].content is None:
            raise LLMError("llm_empty_response")
        content = candidates[0].content
        # Keep the model turn verbatim: Gemini requires thought signatures to be sent back.
        self.history.append(content)
        turn = Turn(
            usage=usage_of(response),
            finish_reason=str(candidates[0].finish_reason) if candidates[0].finish_reason else None,
        )
        texts = []
        for index, part in enumerate(content.parts or []):
            if part.function_call is not None:
                call_id = part.function_call.id
                if call_id:
                    self.native_ids.add(call_id)
                turn.tool_calls.append(
                    ToolCall(
                        id=call_id or f"call-{len(self.history)}-{index}",
                        name=part.function_call.name or "",
                        arguments=dict(part.function_call.args or {}),
                    )
                )
            elif part.text and not part.thought:
                texts.append(part.text)
        turn.text = "\n".join(texts) or None
        return turn


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        temperature: float | None = None,
        client: Any = None,
        thinking_level: str | None = None,
    ):
        self.model = model
        self.temperature = temperature
        self.thinking_level = thinking_level
        self.client = client or genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_seconds * 1000),
                # Overload (503) is common on shared tiers; retries stay within the agent deadline.
                retry_options=types.HttpRetryOptions(
                    attempts=4, initial_delay=2, max_delay=10, http_status_codes=RETRY_STATUS
                ),
            ),
        )

    def start(
        self, *, system: str, user: str, tools: list[ToolSpec], max_output_tokens: int
    ) -> GeminiSession:
        return GeminiSession(self, system, user, tools, max_output_tokens)
