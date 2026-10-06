import asyncio
import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseReasoningItem,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails
from test_agent import analyze
from test_analysis import TARGET

from app.config.models import LlmConfig
from app.llm.base import LLMError, ToolResult, ToolSpec
from app.llm.factory import create_provider
from app.llm.openai_provider import OpenAIProvider, error_code

TOOL = ToolSpec("get_contract_info", "Info.", {"type": "object", "properties": {}})
REQUEST = httpx.Request("POST", "https://api.openai.example/v1/responses")


def function_call(name, arguments, call_id="call_1"):
    return ResponseFunctionToolCall(
        type="function_call", call_id=call_id, name=name, arguments=json.dumps(arguments)
    )


def response(*items, input_tokens=100, output_tokens=30, reasoning=10):
    usage = ResponseUsage(
        input_tokens=input_tokens,
        input_tokens_details=InputTokensDetails(cached_tokens=0, cache_write_tokens=0),
        output_tokens=output_tokens,
        output_tokens_details=OutputTokensDetails(reasoning_tokens=reasoning),
        total_tokens=input_tokens + output_tokens,
    )
    return SimpleNamespace(output=list(items), usage=usage, status="completed")


class FakeResponses:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    async def create(self, **request):
        # Deep copy: the session keeps mutating its history list.
        self.requests.append(json.loads(json.dumps(request)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def provider(replies, **options):
    responses = FakeResponses(replies)
    client = SimpleNamespace(responses=responses)
    return OpenAIProvider("key", "gpt-test", 30, client=client, **options), responses


def test_request_forces_tools_is_stateless_and_parses_calls():
    reasoning = ResponseReasoningItem(
        id="rs_1", type="reasoning", summary=[], encrypted_content="opaque"
    )
    llm, responses = provider(
        [response(reasoning, function_call("get_contract_info", {"address": TARGET}))],
        reasoning_effort="low",
    )
    session = llm.start(system="sys", user="hello", tools=[TOOL], max_output_tokens=512)
    turn = asyncio.run(session.step([]))
    assert [(c.id, c.name, c.arguments) for c in turn.tool_calls] == [
        ("call_1", "get_contract_info", {"address": TARGET})
    ]
    assert (turn.usage.input_tokens, turn.usage.output_tokens, turn.usage.thinking_tokens) == (
        100,
        20,
        10,
    )
    request = responses.requests[0]
    assert request["model"] == "gpt-test" and request["instructions"] == "sys"
    assert request["input"] == [{"role": "user", "content": "hello"}]
    assert request["tool_choice"] == "required" and request["store"] is False
    assert request["reasoning"] == {"effort": "low"}
    assert request["include"] == ["reasoning.encrypted_content"]
    assert request["max_output_tokens"] == 512 and "temperature" not in request
    assert request["tools"] == [
        {
            "type": "function",
            "name": "get_contract_info",
            "description": "Info.",
            "parameters": TOOL.parameters,
            "strict": False,
        }
    ]


def test_history_resends_model_items_and_returns_results_by_call_id():
    reasoning = ResponseReasoningItem(
        id="rs_1", type="reasoning", summary=[], encrypted_content="opaque"
    )
    first = function_call("get_contract_info", {})
    message = ResponseOutputMessage(
        id="msg_1",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text="texto", annotations=[])],
    )
    llm, responses = provider([response(reasoning, first), response(message)], temperature=0.2)
    session = llm.start(system="sys", user="hello", tools=[TOOL], max_output_tokens=512)
    turn = asyncio.run(session.step([]))
    asyncio.run(session.step([ToolResult(turn.tool_calls[0], {"evidence_id": "tool.1"})], "note"))
    second = responses.requests[1]
    assert second["temperature"] == 0.2
    items = second["input"]
    assert items[0] == {"role": "user", "content": "hello"}
    assert items[1]["type"] == "reasoning" and items[1]["encrypted_content"] == "opaque"
    assert items[2]["type"] == "function_call" and items[2]["call_id"] == "call_1"
    assert items[3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": json.dumps({"evidence_id": "tool.1"}),
    }
    assert items[4] == {"role": "user", "content": "note"}


def test_plain_text_and_invalid_arguments_are_returned_for_correction():
    message = ResponseOutputMessage(
        id="msg_1",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text="olá", annotations=[])],
    )
    broken = ResponseFunctionToolCall(
        type="function_call", call_id="c2", name="submit_analysis", arguments="{not json"
    )
    llm, _ = provider([response(message, broken)])
    turn = asyncio.run(llm.start(system="s", user="u", tools=[], max_output_tokens=10).step([]))
    assert turn.text == "olá"
    assert turn.tool_calls[0].arguments == {"_invalid_json": True}


def status_error(status, code=None):
    return openai.APIStatusError(
        "provider detail with secret-key",
        response=httpx.Response(status, request=REQUEST),
        body={"code": code} if code else None,
    )


@pytest.mark.parametrize(
    "exc,code",
    [
        (openai.APITimeoutError(request=REQUEST), "llm_timeout"),
        (openai.APIConnectionError(request=REQUEST), "llm_connection_failed"),
        (status_error(401), "llm_access_denied"),
        (status_error(404), "llm_model_not_found"),
        (status_error(429, "rate_limit_exceeded"), "llm_rate_limited"),
        (status_error(429, "insufficient_quota"), "llm_quota_exceeded"),
        (status_error(503), "llm_unavailable"),
        (status_error(400), "llm_bad_request"),
    ],
)
def test_errors_map_to_safe_codes(exc, code):
    assert error_code(exc) == code
    llm, _ = provider([exc])
    session = llm.start(system="s", user="u", tools=[], max_output_tokens=10)
    with pytest.raises(LLMError) as raised:
        asyncio.run(session.step([]))
    assert raised.value.code == code and "secret" not in str(raised.value)


def test_empty_output_is_an_error():
    llm, _ = provider([response()])
    with pytest.raises(LLMError, match="llm_empty_response"):
        asyncio.run(llm.start(system="s", user="u", tools=[], max_output_tokens=10).step([]))


def test_factory_builds_openai_from_environment_key():
    config = LlmConfig(provider="OpenAI", model="gpt-5-mini", thinking_level="medium")
    llm, issue = create_provider(config, {"OPENAI_API_KEY": "sk-test"})
    assert issue is None and isinstance(llm, OpenAIProvider)
    assert (llm.name, llm.model, llm.reasoning_effort) == ("openai", "gpt-5-mini", "medium")
    assert create_provider(config, {}) == (None, "llm_api_key_missing")


def test_reasoning_and_prices_accept_empty_or_none_from_environment():
    config = LlmConfig.model_validate(
        {
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "thinking_level": "none",
            "input_price_per_million": "",
            "output_price_per_million": "2.00",
        }
    )
    assert config.thinking_level is None and config.input_price_per_million is None
    assert config.output_price_per_million == 2.0


def test_agent_loop_runs_end_to_end_through_the_openai_adapter():
    def submit(results):
        evidence_id = json.loads(results)["evidence_id"]
        return response(
            function_call(
                "submit_analysis",
                {
                    "summary": "Transferência de tokens.",
                    "findings": [
                        {
                            "kind": "source",
                            "statement": "A função exige valor positivo.",
                            "evidence_ids": [evidence_id],
                        }
                    ],
                    "likely_causes": [],
                    "next_steps": [],
                    "security_notes": [],
                    "uncertainties": [],
                },
                call_id="call_2",
            )
        )

    class Responses(FakeResponses):
        async def create(self, **request):
            if len(self.requests) == 1:
                self.replies.append(submit(request["input"][-1]["output"]))
            return await super().create(**request)

    responses = Responses(
        [response(function_call("get_contract_source", {"address": TARGET, "symbol": "transfer"}))]
    )
    llm = OpenAIProvider(
        "key",
        "gpt-test",
        30,
        client=SimpleNamespace(responses=responses),
        reasoning_effort="low",
    )
    result = analyze(llm)
    explanation = result["explanation"]
    assert explanation["findings"][0]["evidence_ids"] == ["tool.1.get_contract_source"]
    usage = explanation["usage"]
    assert (usage["provider"], usage["model"], usage["steps"]) == ("openai", "gpt-test", 2)
    assert (usage["input_tokens"], usage["output_tokens"], usage["thinking_tokens"]) == (
        200,
        40,
        20,
    )
