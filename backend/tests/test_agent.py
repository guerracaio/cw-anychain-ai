import json

import httpx
from fastapi.testclient import TestClient
from llm_fakes import FakeProvider, call, submit
from test_analysis import (
    CALLDATA,
    SENDER,
    TARGET,
    TOKEN_SOURCE,
    TRANSFER_ABI,
    TX,
    config,
    rpc_response,
    token_handler,
)

from app.agent.prompts import evidence_package
from app.llm.base import LLMError
from app.main import create_app

BALANCE_OF = {
    "type": "function",
    "name": "balanceOf",
    "stateMutability": "view",
    "inputs": [{"name": "owner", "type": "address"}],
    "outputs": [{"name": "", "type": "uint256"}],
}
CONTRACT = {"name": "Token", "file_path": "src/Token.sol", "source_code": TOKEN_SOURCE}


def handler(rpc_calls: list | None = None, **options):
    options.setdefault("contract", CONTRACT)
    explorer = token_handler(abi=[*TRANSFER_ABI, BALANCE_OF], **options)

    async def route(request):
        if request.method == "POST":
            body = json.loads(request.content)
            if rpc_calls is not None:
                rpc_calls.append(body)
            if body["method"] == "eth_call":
                result = "0x" + f"{12345:064x}"
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": body["id"], "result": result}
                )
            if body["method"] == "eth_getTransactionByHash":
                response = rpc_response(request)
                data = json.loads(response.content)
                data["result"]["input"] = CALLDATA
                return httpx.Response(200, json=data)
            return rpc_response(request)
        return await explorer(request)

    return route


def analyze(provider, settings=None, mode="developer", **options):
    app = create_app(
        settings or config(rpc=False),
        transport=httpx.MockTransport(handler(**options)),
        llm=provider,
    )
    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"tx_hash": TX, "mode": mode})
        assert response.status_code == 200, response.text
        return response.json()


def test_explanation_keeps_only_valid_citations():
    provider = FakeProvider(
        [
            submit(
                summary="Transferência de 5 unidades.",
                findings=[
                    {
                        "kind": "decoded",
                        "statement": "Chamou transfer.",
                        "evidence_ids": ["decoder.input", "invented.id"],
                    },
                    {"kind": "inference", "statement": "Sem fonte.", "evidence_ids": ["nope"]},
                ],
                security_notes=[{"description": "Revisar.", "evidence_ids": ["decoder.input"]}],
            )
        ]
    )
    result = analyze(provider)
    explanation = result["explanation"]
    assert explanation["summary"] == "Transferência de 5 unidades."
    assert explanation["findings"] == [
        {"kind": "decoded", "statement": "Chamou transfer.", "evidence_ids": ["decoder.input"]}
    ]
    assert explanation["discarded_items"] == 1
    assert explanation["security_notes"][0]["evidence_ids"] == ["decoder.input"]
    usage = explanation["usage"]
    assert (usage["provider"], usage["steps"], usage["input_tokens"]) == ("fake", 1, 100)
    assert usage["estimated_cost_usd"] is None
    assert result["explanation_issue"] is None
    # The deterministic evidence stays untouched next to the model output.
    assert result["transaction"]["decoded_input"]["function"] == "transfer"


def test_tool_results_become_citable_evidence():
    provider = FakeProvider(
        [
            call("get_contract_source", {"address": TARGET, "symbol": "transfer"}),
            lambda results: submit(
                findings=[
                    {
                        "kind": "source",
                        "statement": "A função exige valor positivo.",
                        "evidence_ids": [results[0].content["evidence_id"]],
                    }
                ]
            ),
        ]
    )
    result = analyze(provider)
    (finding,) = result["explanation"]["findings"]
    assert finding["evidence_ids"] == ["tool.1.get_contract_source"]
    source = next(s for s in result["sources"] if s["id"] == "tool.1.get_contract_source")
    assert source["source_type"] == "blockscout"
    first_result = provider.received[1][0][0].content
    assert "require(value > 0)" in first_result["result"]["excerpts"][0]["code"]
    assert result["explanation"]["usage"]["tool_calls"] == ["get_contract_source"]


def test_rpc_tools_require_a_verified_rpc_and_read_historical_state():
    rpc_calls: list = []
    provider = FakeProvider(
        [
            call("read_contract", {"address": TARGET, "function": "balanceOf", "args": [SENDER]}),
            submit(),
        ]
    )
    result = analyze(provider, config(), rpc_calls=rpc_calls)
    assert {t.name for t in provider.tools} >= {"read_contract", "get_native_balance"}
    output = provider.received[1][0][0].content
    assert output["result"]["outputs"][0]["value"] == "12345"
    eth_call = next(c for c in rpc_calls if c["method"] == "eth_call")
    assert eth_call["params"][1] == hex(42 - 1)
    assert eth_call["params"][0]["data"].startswith("0x70a08231")
    assert any(s["id"] == "tool.1.read_contract" for s in result["sources"])

    no_rpc = FakeProvider([submit()])
    analyze(no_rpc)
    assert "read_contract" not in {t.name for t in no_rpc.tools}


def test_tool_errors_are_returned_to_the_model():
    provider = FakeProvider(
        [
            [
                call("read_contract", {"address": TARGET, "function": "transfer"}, "a"),
                call("get_contract_source", {"address": "bad"}, "b"),
                call("drop_database", {}, "c"),
            ],
            submit(),
        ]
    )
    analyze(provider, config())
    errors = [r.content for r in provider.received[1][0]]
    assert errors[0]["error"] == "function_not_found"
    assert errors[1] == {"error": "invalid_arguments", "fields": ["address", "symbol"]}
    assert errors[2] == {"error": "unknown_tool"}


def test_invalid_submission_and_plain_text_are_corrected():
    provider = FakeProvider(["texto solto", submit(summary=""), submit()])
    result = analyze(provider)
    assert provider.received[1][1].startswith("Respond only with tool calls")
    assert provider.received[2][0][0].content["error"] == "invalid_submission"
    assert result["explanation"]["usage"]["steps"] == 3


def test_step_limit_and_provider_errors_keep_the_evidence():
    looping = FakeProvider(
        [call("get_contract_info", {"address": TARGET}, str(i)) for i in range(3)]
    )
    settings = config(rpc=False, max_agent_steps=2)
    result = analyze(looping, settings)
    assert result["explanation"] is None
    assert result["explanation_issue"] == "agent_step_limit"
    assert "last step" in looping.received[1][1]
    assert result["transaction"]["decoded_input"] is not None

    failing = FakeProvider([LLMError("llm_rate_limited")])
    assert analyze(failing)["explanation_issue"] == "llm_rate_limited"


def test_without_provider_the_reason_is_reported():
    result = analyze(None)
    assert result["explanation"] is None
    assert result["explanation_issue"] == "llm_not_configured"


def test_prompt_marks_evidence_as_untrusted_and_carries_mode():
    provider = FakeProvider([submit()])
    analyze(provider, mode="auditor")
    assert "untrusted DATA" in provider.system
    assert provider.user.startswith("Audience: auditor")
    assert "<evidence>" in provider.user and "require(value > 0)" in provider.user


def test_evidence_package_respects_size_budget():
    provider = FakeProvider([submit()])
    analyze(provider)
    package = provider.user.split("<evidence>\n", 1)[1]
    assert len(package) < 60000
    from app.domain.analysis import AnalysisResponse, SourceExcerpt
    from app.domain.transaction import TransactionDetails

    response = AnalysisResponse(
        network="n",
        tx_hash=TX,
        status="success",
        summary="",
        request_id="r",
        explorer_url="https://x",
        transaction=TransactionDetails(native_currency="ETH"),
        contract_context=[
            SourceExcerpt(
                origin="explorer",
                contract="C",
                symbol="C.f",
                kind="function",
                reason="called_function",
                path="C.sol",
                start_line=1,
                end_line=2,
                code="x" * 10000,
                evidence_ids=[],
            )
        ],
    )
    assert "x" * 100 not in evidence_package(response, 4000)


def test_plain_value_transfer_is_answered_without_tools():
    provider = FakeProvider([submit(summary="Transferência simples.")])
    app = create_app(
        config(rpc=False),
        transport=httpx.MockTransport(
            token_handler(
                tx={
                    "hash": TX,
                    "from": {"hash": SENDER},
                    "to": {"hash": TARGET, "is_contract": False},
                    "value": "2000000000000000",
                    "raw_input": "0x",
                    "status": "ok",
                    "block_number": 42,
                },
                log_addresses=(),
            )
        ),
        llm=provider,
    )
    with TestClient(app) as client:
        result = client.post("/api/analyze", json={"tx_hash": TX}).json()
    assert [t.name for t in provider.tools] == ["submit_analysis"]
    assert result["transaction"]["recipient_is_contract"] is False
    assert result["explanation"]["summary"] == "Transferência simples."
