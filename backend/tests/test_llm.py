import asyncio

import httpx
import pytest
from google.genai import errors, types

from app.config.models import LlmConfig
from app.llm.base import LLMError, ToolResult, ToolSpec
from app.llm.factory import create_provider
from app.llm.gemini_provider import GeminiProvider, error_code

TOOL = ToolSpec("get_contract_info", "Info.", {"type": "object", "properties": {}})


def response(parts, prompt=10, output=5, thoughts=2):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt,
            candidates_token_count=output,
            thoughts_token_count=thoughts,
        ),
    )


class FakeModels:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    async def generate_content(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": list(contents), "config": config})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeClient:
    def __init__(self, replies):
        self.models = FakeModels(replies)
        self.aio = self


def provider(replies):
    client = FakeClient(replies)
    return GeminiProvider("key", "gemini-test", 30, client=client), client.models


def test_session_sends_tools_forces_tool_calls_and_parses_calls():
    signed = types.Part(
        function_call=types.FunctionCall(id="c1", name="get_contract_info", args={"a": 1}),
        thought_signature=b"signature",
    )
    gemini, models = provider([response([types.Part(text="pensando", thought=True), signed])])
    session = gemini.start(system="sys", user="hello", tools=[TOOL], max_output_tokens=512)
    turn = asyncio.run(session.step([]))
    assert [(c.id, c.name, c.arguments) for c in turn.tool_calls] == [
        ("c1", "get_contract_info", {"a": 1})
    ]
    assert turn.text is None
    assert (turn.usage.input_tokens, turn.usage.output_tokens, turn.usage.thinking_tokens) == (
        10,
        5,
        2,
    )
    config = models.requests[0]["config"]
    assert config.system_instruction == "sys" and config.max_output_tokens == 512
    assert config.automatic_function_calling.disable is True
    assert config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.ANY
    declaration = config.tools[0].function_declarations[0]
    assert declaration.name == "get_contract_info"
    assert declaration.parameters_json_schema == TOOL.parameters
    assert models.requests[0]["model"] == "gemini-test"


def test_history_keeps_model_turn_verbatim_and_returns_results():
    first = types.Part(
        function_call=types.FunctionCall(id="c1", name="get_contract_info", args={}),
        thought_signature=b"signature",
    )
    unnamed = types.Part(function_call=types.FunctionCall(name="get_contract_info", args={}))
    gemini, models = provider([response([first, unnamed]), response([types.Part(text="ok")])])
    session = gemini.start(system="s", user="u", tools=[TOOL], max_output_tokens=256)
    turn = asyncio.run(session.step([]))
    results = [
        ToolResult(call, {"evidence_id": f"tool.{i}"}) for i, call in enumerate(turn.tool_calls)
    ]
    final = asyncio.run(session.step(results, note="last step"))
    assert final.text == "ok" and not final.tool_calls
    history = models.requests[1]["contents"]
    assert history[1].parts[0].thought_signature == b"signature"
    replies = history[2].parts
    assert replies[0].function_response.id == "c1"
    # Calls without a provider id are answered by name only.
    assert replies[1].function_response.id is None
    assert replies[1].function_response.response == {"evidence_id": "tool.1"}
    assert replies[2].text == "last step"


@pytest.mark.parametrize(
    "exc,code",
    [
        (errors.ClientError(429, {"error": {"message": "quota"}}), "llm_rate_limited"),
        (errors.ClientError(403, {"error": {"message": "key"}}), "llm_access_denied"),
        (errors.ClientError(404, {"error": {"message": "model"}}), "llm_model_not_found"),
        (errors.ClientError(400, {"error": {"message": "bad"}}), "llm_bad_request"),
        (errors.ServerError(503, {"error": {"message": "down"}}), "llm_unavailable"),
        (httpx.ReadTimeout("slow"), "llm_timeout"),
    ],
)
def test_errors_map_to_safe_codes(exc, code):
    assert error_code(exc) == code
    gemini, _ = provider([exc])
    session = gemini.start(system="s", user="u", tools=[TOOL], max_output_tokens=256)
    with pytest.raises(LLMError) as raised:
        asyncio.run(session.step([]))
    assert raised.value.code == code
    assert "quota" not in str(raised.value) and raised.value.__cause__ is None


def test_empty_candidates_are_an_error():
    gemini, _ = provider([types.GenerateContentResponse(candidates=[])])
    session = gemini.start(system="s", user="u", tools=[TOOL], max_output_tokens=256)
    with pytest.raises(LLMError, match="llm_empty_response"):
        asyncio.run(session.step([]))


@pytest.mark.parametrize(
    "config,environ,issue",
    [
        ({}, {}, "llm_not_configured"),
        ({"provider": "gemini"}, {}, "llm_model_not_configured"),
        ({"provider": "gemini", "model": "m"}, {}, "llm_api_key_missing"),
        (
            {"provider": "anthropic", "model": "m"},
            {"ANTHROPIC_API_KEY": "k"},
            "llm_provider_not_supported",
        ),
    ],
)
def test_factory_reports_why_the_agent_is_disabled(config, environ, issue):
    assert create_provider(LlmConfig.model_validate(config), environ) == (None, issue)


def test_factory_reads_provider_key_from_environment():
    llm, issue = create_provider(
        LlmConfig.model_validate({"provider": "Gemini", "model": "gemini-x"}),
        {"GEMINI_API_KEY": "secret-key"},
    )
    assert issue is None and isinstance(llm, GeminiProvider)
    assert (llm.name, llm.model) == ("gemini", "gemini-x")
