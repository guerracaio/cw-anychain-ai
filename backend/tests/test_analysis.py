import asyncio
import json

import httpx
from fastapi.testclient import TestClient
from github_mock import COMMIT, FakeGitHub, artifact

from app.blockchain.decoder import event_topic
from app.config.models import AppConfig, RepositoryConfig
from app.main import create_app

TX = "0x" + "ab" * 32
SENDER = "0x" + "11" * 20
TARGET = "0x" + "22" * 20
BLOCK = "0x" + "33" * 32
BASE_TX = {
    "hash": TX,
    "from": {"hash": SENDER},
    "to": {"hash": TARGET},
    "value": "1000000000000000001",
    "raw_input": "0x",
    "status": "ok",
    "block_number": 42,
    "block_hash": BLOCK,
    "gas_used": "21000",
}


def config(rpc=True, **analysis):
    return AppConfig.model_validate(
        {
            "network": {
                "id": "custom-chain",
                "name": "Custom Chain",
                "chain_id": 321,
                "native_currency": "COIN",
            },
            "explorer": {"base_url": "https://explorer.example"},
            "rpc": {"url": "https://rpc.example/secret-key" if rpc else None},
            "analysis": analysis,
        }
    )


def analyze(handler, settings=None):
    with TestClient(
        create_app(settings or config(), transport=httpx.MockTransport(handler))
    ) as client:
        response = client.post("/api/analyze", json={"tx_hash": TX})
        assert response.status_code == 200, response.text
        assert "secret-key" not in response.text
        return response.json()


def rpc_response(request, *, status="0x1", chain="0x141", missing=False):
    body = json.loads(request.content)
    result = {
        "eth_chainId": chain,
        "eth_getTransactionByHash": {
            "hash": TX,
            "from": SENDER,
            "to": TARGET,
            "value": "0xde0b6b3a7640001",
            "input": "0x",
            "blockNumber": "0x2a",
            "blockHash": BLOCK,
        },
        "eth_getTransactionReceipt": {
            "transactionHash": TX,
            "status": status,
            "blockNumber": "0x2a",
            "blockHash": BLOCK,
            "gasUsed": "0x5208",
            "logs": [],
        },
        # Replays of plain transfers succeed with empty return data.
        "eth_call": "0x",
    }[body["method"]]
    if missing and body["method"] != "eth_chainId":
        result = None
    if body["method"] == "eth_call" and status == "0x0":
        # A failed transaction reverts again when replayed (bare revert, no data).
        error = {"code": 3, "message": "execution reverted", "data": "0x"}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": error})
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})


def explorer_response(request, tx=None):
    if request.url.path.endswith(TX):
        return httpx.Response(200, json=tx or BASE_TX)
    return httpx.Response(200, json={"items": [], "next_page_params": None})


def test_successful_collection_exact_value_and_traceable_sources():
    result = analyze(
        lambda req: rpc_response(req) if req.method == "POST" else explorer_response(req)
    )
    assert result["status"] == "success"
    assert result["transaction"]["value_display"] == "1.000000000000000001"
    assert result["transaction"]["gas_used"] == "21000"
    ids = {s["id"] for s in result["sources"]}
    assert set(result["status_evidence_ids"]) <= ids
    for refs in result["transaction"]["field_sources"].values():
        assert set(refs) <= ids
    assert result["explorer_url"].endswith(TX)


def test_rpc_fallback_when_explorer_is_unavailable():
    result = analyze(lambda req: rpc_response(req) if req.method == "POST" else httpx.Response(404))
    assert result["status"] == "success"
    assert result["transaction"]["sender"] == SENDER
    assert any("explorer.transaction" in value for value in result["uncertainties"])


def test_mismatched_rpc_network_is_never_used():
    methods = []

    def handler(request):
        if request.method == "POST":
            methods.append(json.loads(request.content)["method"])
            return rpc_response(request, chain="0x1")
        return explorer_response(request)

    result = analyze(handler)
    assert methods == ["eth_chainId"]
    assert result["status"] == "success"
    assert not any(s["source_type"] == "rpc" for s in result["sources"])
    assert any("chain_mismatch" in issue for issue in result["uncertainties"])


def test_null_results_do_not_mean_pending():
    result = analyze(
        lambda req: rpc_response(req, missing=True) if req.method == "POST" else httpx.Response(404)
    )
    assert result["status"] == "unknown"
    assert result["transaction"]["value_display"] is None
    assert any("resultado nulo" in issue for issue in result["uncertainties"])


def test_failure_has_no_invented_cause_or_successful_value_transfer():
    result = analyze(
        lambda req: (
            rpc_response(req, status="0x0")
            if req.method == "POST"
            else explorer_response(req, {**BASE_TX, "status": "error"})
        )
    )
    assert result["status"] == "failed"
    assert result["diagnosis"]["confirmed"]
    assert result["diagnosis"]["likely_causes"] == []
    assert "não deve" in result["summary"]


def test_conflicting_status_is_reported():
    result = analyze(
        lambda req: (
            rpc_response(req, status="0x0") if req.method == "POST" else explorer_response(req)
        )
    )
    assert result["status"] == "unknown"
    assert result["diagnosis"]["confirmed"] == []


def test_deadline_preserves_completed_evidence():
    async def handler(request):
        if request.url.path.endswith(TX):
            return explorer_response(request)
        await asyncio.sleep(1)
        return explorer_response(request)

    result = analyze(handler, config(rpc=False, collection_timeout_seconds=0.05))
    assert result["status"] == "success"
    assert result["sources"][0]["id"] == "explorer.transaction"
    assert any("timeout" in issue for issue in result["uncertainties"])


def test_explorer_only_retains_logs_and_indexed_transfers():
    def handler(request):
        if request.url.path.endswith("/logs"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "address": {"hash": TARGET},
                            "data": "0x01",
                            "topics": ["0x" + "44" * 32],
                            "index": 0,
                        }
                    ],
                    "next_page_params": None,
                },
            )
        if request.url.path.endswith("/token-transfers"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "transaction_hash": TX,
                            "from": {"hash": SENDER},
                            "to": {"hash": TARGET},
                            "token": {"symbol": "T", "type": "ERC-20", "address_hash": TARGET},
                            "total": {"value": "12345678901234567890", "decimals": "18"},
                        }
                    ],
                    "next_page_params": None,
                },
            )
        return explorer_response(request)

    result = analyze(handler, config(rpc=False))
    assert result["events"][0]["data"] == "0x01"
    assert result["transfers"][0]["total"]["value"] == "12345678901234567890"
    assert any("RPC não configurado" in issue for issue in result["uncertainties"])


def test_deadline_retains_first_page_of_slow_collection():
    async def handler(request):
        if request.url.path.endswith("/logs"):
            if request.url.params:
                await asyncio.sleep(1)
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"address": {"hash": TARGET}, "data": "0x", "topics": [], "index": 0}
                    ],
                    "next_page_params": {"index": 1},
                },
            )
        return explorer_response(request)

    result = analyze(handler, config(rpc=False, collection_timeout_seconds=0.1))
    assert len(result["events"]) == 1
    assert any("timeout" in issue for issue in result["uncertainties"])
    log_source = next(s for s in result["sources"] if s["id"] == "explorer.logs")
    assert log_source["payload"]["complete"] is False


def test_provider_errors_do_not_leak_credentials_in_logs(caplog):
    def handler(request):
        if request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"message": "secret-key"}}
            )
        return explorer_response(request)

    result = analyze(handler)
    assert "secret-key" not in caplog.text
    assert any("rpc_error" in issue for issue in result["uncertainties"])


TRANSFER_ABI = [
    {
        "type": "function",
        "name": "transfer",
        "inputs": [{"name": "to", "type": "address"}, {"name": "value", "type": "uint256"}],
    },
    {
        "type": "event",
        "name": "Transfer",
        "inputs": [
            {"name": "from", "type": "address", "indexed": True},
            {"name": "to", "type": "address", "indexed": True},
            {"name": "value", "type": "uint256", "indexed": False},
        ],
    },
]
TRANSFER_TOPIC = event_topic(TRANSFER_ABI[1])
CALLDATA = "0xa9059cbb" + "00" * 12 + "11" * 20 + f"{5:064x}"


def token_handler(
    abi=TRANSFER_ABI, tx=None, delay=0.0, log_addresses=(TARGET,), contract=None, github=None
):
    async def handler(request):
        if github and github.handles(request):
            return github(request)
        path = request.url.path
        if path.startswith("/api/v2/smart-contracts/"):
            await asyncio.sleep(delay)
            body = {"is_verified": abi is not None, "abi": abi, **(contract or {})}
            return httpx.Response(200, json=body)
        if path.endswith("/logs"):
            items = [
                {
                    "address": {"hash": account},
                    "data": f"0x{5:064x}",
                    "topics": [TRANSFER_TOPIC, "0x" + "00" * 12 + SENDER[2:], "0x" + "00" * 32],
                    "index": index,
                }
                for index, account in enumerate(log_addresses)
            ]
            return httpx.Response(200, json={"items": items, "next_page_params": None})
        return explorer_response(request, tx or {**BASE_TX, "raw_input": CALLDATA})

    return handler


def test_call_and_events_are_decoded_with_traceable_abi_evidence():
    result = analyze(token_handler(), config(rpc=False))
    decoded = result["transaction"]["decoded_input"]
    assert decoded["signature"] == "transfer(address,uint256)"
    assert [a["value"] for a in decoded["arguments"]] == ["0x" + "11" * 20, "5"]
    assert decoded["evidence_ids"] == [
        "decoder.input",
        f"explorer.abi.{TARGET}",
        "explorer.transaction",
    ]
    event = result["events"][0]["decoded"]
    assert event["signature"] == "Transfer(address,address,uint256)"
    assert event["arguments"][0]["value"] == SENDER
    ids = {s["id"] for s in result["sources"]}
    assert set(decoded["evidence_ids"]) | set(event["evidence_ids"]) <= ids
    abi_source = next(s for s in result["sources"] if s["id"] == f"explorer.abi.{TARGET}")
    assert abi_source["source_url"] == f"https://explorer.example/api/v2/smart-contracts/{TARGET}"
    assert "transfer(address,uint256)" in result["summary"]
    assert not any("seletor" in issue for issue in result["uncertainties"])


def test_missing_abi_preserves_raw_data_and_explains_limits():
    result = analyze(token_handler(abi=None), config(rpc=False))
    assert result["transaction"]["decoded_input"] is None
    assert result["transaction"]["selector"] == "0xa9059cbb"
    assert result["events"][0]["decoded"] is None
    assert result["events"][0]["topics"][0] == TRANSFER_TOPIC
    assert not any(s["source_type"] == "decoder" for s in result["sources"])
    issues = " ".join(result["uncertainties"])
    assert "não confirma a identidade da função" in issues
    assert "ABI não verificada" in issues and "nenhum repositório configurado" in issues
    assert "transfer" not in result["summary"]


def test_selector_absent_from_abi_is_not_decoded():
    abi = [{"type": "function", "name": "approve", "inputs": []}]
    result = analyze(token_handler(abi=abi), config(rpc=False))
    assert result["transaction"]["decoded_input"] is None
    assert any("seletor não consta" in issue for issue in result["uncertainties"])


def test_abi_resolution_respects_analysis_deadline():
    result = analyze(token_handler(delay=1), config(rpc=False, collection_timeout_seconds=0.3))
    assert result["transaction"]["decoded_input"] is None
    assert any("tempo limite da análise" in issue for issue in result["uncertainties"])


def test_contract_limit_is_reported():
    emitters = ("0x" + "44" * 20, "0x" + "55" * 20)
    result = analyze(token_handler(log_addresses=emitters), config(rpc=False, max_abi_contracts=2))
    assert result["transaction"]["decoded_input"] is not None
    assert result["events"][0]["decoded"] is not None
    assert result["events"][1]["decoded"] is None
    assert any("max_abi_contracts" in issue for issue in result["uncertainties"])


TOKEN_SOURCE = """contract Token {
    /// @notice Moves tokens; the text here is untrusted evidence.
    function transfer(address to, uint256 value) external returns (bool) {
        require(value > 0);
        return true;
    }
}
"""


def with_repository(settings, **repository):
    return settings.model_copy(
        update={
            "repositories": [
                RepositoryConfig.model_validate(
                    {"url": "https://github.com/org/contracts", **repository}
                )
            ]
        }
    )


def test_verified_explorer_source_grounds_the_called_function():
    contract = {"name": "Token", "file_path": "src/Token.sol", "source_code": TOKEN_SOURCE}
    result = analyze(token_handler(contract=contract), config(rpc=False))
    context = result["contract_context"]
    assert [(c["origin"], c["symbol"], c["reason"]) for c in context] == [
        ("explorer", "Token", "contract_header"),
        ("explorer", "Token.transfer", "called_function"),
    ]
    assert context[1]["start_line"] == 2 and context[1]["end_line"] == 6
    assert "require(value > 0)" in context[1]["code"]
    ids = {s["id"] for s in result["sources"]}
    assert context[1]["evidence_ids"] == [f"explorer.source.{TARGET}"]
    assert set(context[1]["evidence_ids"]) <= ids


def test_repository_excerpts_cite_commit_path_and_lines():
    github = FakeGitHub({"src/Token.sol": TOKEN_SOURCE})
    result = analyze(
        token_handler(contract={"name": "Token"}, github=github),
        with_repository(config(rpc=False)),
    )
    excerpt = result["contract_context"][1]
    assert excerpt["origin"] == "repository" and excerpt["commit"] == COMMIT
    assert excerpt["url"] == (f"https://github.com/org/contracts/blob/{COMMIT}/src/Token.sol#L2-L6")
    source = next(s for s in result["sources"] if s["id"] == excerpt["evidence_ids"][0])
    assert source["source_type"] == "repository"
    assert source["payload"]["commit"] == COMMIT
    assert any("pode diferir do código implantado" in u for u in result["uncertainties"])


def test_repository_abi_fallback_decodes_mapped_unverified_contract():
    github = FakeGitHub(
        {"out/Token.sol/Token.json": artifact(TRANSFER_ABI), "src/Token.sol": TOKEN_SOURCE}
    )
    settings = with_repository(config(rpc=False), contracts=[{"address": TARGET, "name": "Token"}])
    result = analyze(token_handler(abi=None, github=github), settings)
    decoded = result["transaction"]["decoded_input"]
    assert decoded["abi_source"] == "repository"
    assert decoded["evidence_ids"][1] == f"repository.abi.{TARGET}"
    assert result["contract_context"][1]["symbol"] == "Token.transfer"
    assert any("não confirmou que esse artefato" in u for u in result["uncertainties"])


def test_without_source_or_repository_the_limitation_is_explicit():
    result = analyze(token_handler(contract={"name": "Token"}), config(rpc=False))
    assert result["contract_context"] == []
    assert any("Sem código-fonte verificado de Token" in u for u in result["uncertainties"])


def test_github_failure_keeps_explorer_context():
    github = FakeGitHub({})
    github.status = 403
    contract = {"name": "Token", "file_path": "src/Token.sol", "source_code": TOKEN_SOURCE}
    result = analyze(
        token_handler(contract=contract, github=github), with_repository(config(rpc=False))
    )
    assert {c["origin"] for c in result["contract_context"]} == {"explorer"}
    assert any("GITHUB_TOKEN" in u for u in result["uncertainties"])
