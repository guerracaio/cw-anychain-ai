import asyncio
import json

import httpx
import pytest
from eth_abi import encode
from fastapi.testclient import TestClient
from test_analysis import (
    BASE_TX,
    BLOCK,
    CALLDATA,
    SENDER,
    TARGET,
    TRANSFER_ABI,
    TX,
    config,
    token_handler,
)

from app.blockchain.decoder import decode_revert, error_selector
from app.blockchain.rpc_client import RpcClient
from app.main import create_app
from app.repositories.resolver import find_revert_sites
from app.services.http import JsonHttpClient, UpstreamError

INSUFFICIENT = {
    "type": "error",
    "name": "Insufficient",
    "inputs": [{"name": "available", "type": "uint256"}, {"name": "required", "type": "uint256"}],
}
BALANCE_OF = {
    "type": "function",
    "name": "balanceOf",
    "stateMutability": "view",
    "inputs": [{"name": "owner", "type": "address"}],
    "outputs": [{"name": "", "type": "uint256"}],
}
ABI = [*TRANSFER_ABI, INSUFFICIENT, BALANCE_OF]
SOURCE = """contract Token {
    error Insufficient(uint256 available, uint256 required);

    function transfer(address to, uint256 value) external returns (bool) {
        // revert Insufficient(0, 0) in a comment is not a revert site
        if (value > 3) revert Insufficient(3, value);
        return true;
    }
}
"""
FAILED_TX = {
    **BASE_TX,
    "status": "error",
    "raw_input": CALLDATA,
    "gas_limit": "60000",
    "gas_used": "30000",
}


def revert_data(available=3, required=5):
    return (
        error_selector(INSUFFICIENT) + encode(["uint256", "uint256"], [available, required]).hex()
    )


def rpc(request, call_error=None, call_result=None, balance=3, gas_used=30000):
    body = json.loads(request.content)
    method = body["method"]
    reply = {"jsonrpc": "2.0", "id": body["id"]}
    if method == "eth_chainId":
        reply["result"] = "0x141"
    elif method == "eth_getTransactionByHash":
        reply["result"] = {
            "hash": TX,
            "from": SENDER,
            "to": TARGET,
            "value": "0x0",
            "input": CALLDATA,
            "blockNumber": "0x2a",
            "blockHash": BLOCK,
        }
    elif method == "eth_getTransactionReceipt":
        reply["result"] = {
            "transactionHash": TX,
            "status": "0x0",
            "blockNumber": "0x2a",
            "blockHash": BLOCK,
            "gasUsed": hex(gas_used),
            "logs": [],
        }
    elif method == "eth_call":
        if body["params"][0]["data"].startswith("0x70a08231"):
            reply["result"] = "0x" + f"{balance:064x}"
        elif call_error:
            reply["error"] = call_error
        else:
            reply["result"] = call_result or "0x"
    return httpx.Response(200, json=reply)


def analyze(rpc_options=None, tx=FAILED_TX, contract=None, with_rpc=True):
    explorer = token_handler(
        abi=ABI,
        tx=tx,
        contract=contract or {"name": "Token", "file_path": "Token.sol", "source_code": SOURCE},
        log_addresses=(),
    )

    async def route(request):
        if request.method == "POST":
            return rpc(request, **(rpc_options or {}))
        return await explorer(request)

    app = create_app(config(rpc=with_rpc), transport=httpx.MockTransport(route))
    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"tx_hash": TX})
        assert response.status_code == 200, response.text
        return response.json()


def test_replayed_custom_error_is_decoded_located_and_checked_against_state():
    result = analyze({"call_error": {"code": 3, "message": "x", "data": revert_data()}})
    diagnosis = result["diagnosis"]
    assert diagnosis["replay"] == "reverted"
    revert = diagnosis["revert"]
    assert (revert["source"], revert["kind"], revert["signature"]) == (
        "rpc_replay",
        "custom_error",
        "Insufficient(uint256,uint256)",
    )
    assert [a["value"] for a in revert["arguments"]] == ["3", "5"]
    assert revert["evidence_ids"] == ["rpc.replay", f"explorer.abi.{TARGET}"]
    assert any("Insufficient" in f["description"] for f in diagnosis["confirmed"])
    sites = [c for c in result["contract_context"] if c["reason"] == "revert_site"]
    assert [(s["symbol"], s["start_line"]) for s in sites] == [("Token.transfer", 4)]
    assert any(c["reason"] == "error_declaration" for c in result["contract_context"])
    (read,) = diagnosis["state_reads"]
    assert read["signature"] == "balanceOf(address)" and read["block"] == hex(41)
    assert read["outputs"][0]["value"] == "3"
    assert any("Saldo" in c["description"] for c in diagnosis["likely_causes"])
    replay = next(s for s in result["sources"] if s["id"] == "rpc.replay")
    assert replay["payload"]["params"][1] == hex(41)
    assert "secret-key" not in json.dumps(result)


def test_explorer_reason_is_used_when_history_is_unavailable():
    tx = {
        **FAILED_TX,
        "revert_reason": {
            "method_call": "Error(string reason)",
            "method_id": "08c379a0",
            "parameters": [{"name": "reason", "type": "string", "value": "Too little received"}],
        },
    }
    archive = {"code": -32602, "message": "Archive requests require a personal token"}
    result = analyze({"call_error": archive}, tx=tx)
    diagnosis = result["diagnosis"]
    assert diagnosis["replay"] == "unavailable"
    assert diagnosis["revert"]["source"] == "explorer"
    assert diagnosis["revert"]["message"] == "Too little received"
    assert any("archive" in u for u in result["uncertainties"])
    assert "Archive requests" not in json.dumps(result)


def test_successful_replay_with_exhausted_gas_points_to_out_of_gas():
    tx = {**FAILED_TX, "gas_used": "60000", "result": "out of gas"}
    result = analyze({"call_result": "0x", "gas_used": 60000}, tx=tx)
    diagnosis = result["diagnosis"]
    assert diagnosis["replay"] == "succeeded"
    assert diagnosis["revert"]["kind"] == "out_of_gas"
    assert any("Falta de gas" in c["description"] for c in diagnosis["likely_causes"])
    assert any("Todo o gas" in c["description"] for c in diagnosis["confirmed"])


def test_successful_replay_without_gas_issue_points_to_state_change_in_block():
    result = analyze({"call_result": "0x"})
    (cause,) = [
        c for c in result["diagnosis"]["likely_causes"] if "mudou dentro" in c["description"]
    ]
    assert cause["evidence_ids"][0] == "rpc.replay"


def test_without_rpc_or_reason_the_gap_is_explicit():
    result = analyze(with_rpc=False)
    diagnosis = result["diagnosis"]
    assert diagnosis["replay"] == "not_attempted" and diagnosis["revert"] is None
    assert any("Motivo da falha indisponível" in u for u in result["uncertainties"])
    assert any("archive" in step for step in diagnosis["next_steps"])


def test_successful_transactions_are_not_diagnosed():
    result = analyze({"call_result": "0x"}, tx={**BASE_TX, "raw_input": CALLDATA})
    assert result["diagnosis"]["replay"] is None
    assert not any(s["id"] == "rpc.replay" for s in result["sources"])


@pytest.mark.parametrize(
    "data,kind,detail",
    [
        (
            "0x08c379a0" + encode(["string"], ["Price slippage check"]).hex(),
            "error_string",
            "Price slippage check",
        ),
        ("0x4e487b71" + f"{0x11:064x}", "panic", "0x11: overflow ou underflow aritmético"),
        ("0x", "empty", None),
        (None, "empty", None),
        ("0x1234", "unknown", None),
        ("0x08c379a0" + "00", "unknown", None),
        ("0xdeadbeef", "unknown", None),
    ],
)
def test_decode_revert_standard_payloads(data, kind, detail):
    decoded = decode_revert(data, [])
    assert decoded.kind == kind and decoded.message == detail


def test_decode_revert_custom_errors_and_collisions():
    decoded = decode_revert(revert_data(), [(TARGET, [INSUFFICIENT])])
    assert decoded.kind == "custom_error" and decoded.abi_address == TARGET
    assert not decoded.ambiguous
    other = {**INSUFFICIENT, "name": "Insufficient", "inputs": INSUFFICIENT["inputs"]}
    collision = {"type": "error", "name": "Clash", "inputs": []}
    collision_selector = error_selector(collision)
    assert decode_revert(collision_selector, [(TARGET, [other, collision])]).name == "Clash"


def test_revert_sites_match_messages_and_errors_but_not_comments():
    source = (
        SOURCE
        + """
contract Pool {
    function swap() external {
        require(msg.sender != address(0), "Price slippage check");
    }
    function quote() external pure {
        // "Price slippage check" mentioned in a comment
    }
}
"""
    )
    by_error = find_revert_sites(
        {"Token.sol": source}, error="Insufficient", message=None, budget=5000
    )
    assert [(e.symbol, e.reason) for e in by_error] == [
        ("Token.Insufficient", "error_declaration"),
        ("Token.transfer", "revert_site"),
    ]
    by_message = find_revert_sites(
        {"Pool.sol": source}, error=None, message="Price slippage check", budget=5000
    )
    # The same text inside a comment (in quote()) is not a revert site.
    assert [e.symbol for e in by_message] == ["Pool.swap"]


@pytest.mark.parametrize(
    "error,expected",
    [
        ({"code": 3, "message": "execution reverted", "data": "0x1234"}, (True, "0x1234")),
        ({"code": -32000, "message": "execution reverted"}, (True, None)),
        ({"code": -32000, "message": "out of gas"}, (True, None)),
        ({"code": -32000, "message": "missing trie node abc"}, "historical_state_unavailable"),
        ({"code": -32000, "message": "secret-key rejected"}, "rpc_error"),
    ],
)
def test_simulate_classifies_reverts_and_unavailable_state(error, expected):
    def handler(request):
        body = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": error})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rpc_client = RpcClient(JsonHttpClient(client, 1, 4096), "https://rpc.example")
            return await rpc_client.simulate({"to": TARGET, "data": "0x"}, "0x29")

    if isinstance(expected, str):
        with pytest.raises(UpstreamError) as raised:
            asyncio.run(run())
        assert raised.value.code == expected and "secret" not in str(raised.value)
    else:
        outcome = asyncio.run(run())
        assert (outcome.reverted, outcome.data) == expected
