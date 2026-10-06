import asyncio
import json

import httpx
import pytest

from app.blockchain.rpc_client import RpcClient
from app.explorer.blockscout_client import BlockscoutClient
from app.services.http import JsonHttpClient, UpstreamError

TX = "0x" + "ab" * 32
ADDRESS = "0x" + "12" * 20


def run_with_http(handler, operation, *, max_bytes=4096, timeout=1):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await operation(JsonHttpClient(client, timeout, max_bytes))

    return asyncio.run(run())


@pytest.mark.parametrize("status", [401, 404, 500])
def test_http_errors_are_safe_and_not_retried(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="secret-api-key")

    with pytest.raises(UpstreamError) as error:
        run_with_http(
            handler,
            lambda http: http.request("GET", "https://test.example/secret-api-key", tool="test"),
        )
    assert len(calls) == 1
    assert "secret" not in str(error.value)


def test_transient_error_is_retried_once():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json={"ok": True})

    assert run_with_http(
        handler, lambda http: http.request("GET", "https://test.example", tool="test")
    ) == {"ok": True}
    assert len(calls) == 2


@pytest.mark.parametrize(
    "body,code",
    [
        (b"not json", "invalid_json"),
        (b"x" * 100, "payload_limit"),
        (b'{"value": NaN}', "invalid_json"),
        (b'{"value": 1e999}', "invalid_json"),
    ],
)
def test_http_rejects_invalid_and_oversized_payloads(body, code):
    with pytest.raises(UpstreamError, match=code):
        run_with_http(
            lambda req: httpx.Response(200, content=body),
            lambda http: http.request("GET", "https://test.example", tool="test"),
            max_bytes=32,
        )


def test_total_tool_timeout():
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    with pytest.raises(UpstreamError, match="timeout"):
        run_with_http(
            handler,
            lambda http: http.request("GET", "https://test.example", tool="test"),
            timeout=0.01,
        )


def test_explorer_pagination_preserves_base_path_and_partial_results():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                200, json={"items": [{"index": 0}], "next_page_params": {"index": 1}}
            )
        return httpx.Response(404)

    result = run_with_http(
        handler,
        lambda http: BlockscoutClient(
            http, "https://test.example/custom", 3, 10
        ).get_transaction_logs(TX),
    )
    assert result.items == [{"index": 0}]
    assert not result.complete
    assert result.issue == "not_found"
    assert calls[1].url.params["index"] == "1"
    assert calls[1].url.path.startswith("/custom/api/v2/")


@pytest.mark.parametrize(
    "max_pages,max_items,expected",
    [(1, 10, "page_limit"), (5, 1, "collection_limit"), (5, 10, "repeated_pagination")],
)
def test_explorer_collection_is_bounded(max_pages, max_items, expected):
    result = run_with_http(
        lambda req: httpx.Response(
            200, json={"items": [{"index": 0}], "next_page_params": {"index": 1}}
        ),
        lambda http: BlockscoutClient(
            http, "https://test.example", max_pages, max_items
        ).get_transaction_logs(TX),
    )
    assert result.issue == expected
    assert not result.complete


def test_empty_collection_is_distinct_from_missing():
    result = run_with_http(
        lambda req: httpx.Response(200, json={"items": [], "next_page_params": None}),
        lambda http: BlockscoutClient(http, "https://test.example", 3, 10).get_transaction_logs(TX),
    )
    assert result.complete and result.items == [] and len(result.pages) == 1


def test_explorer_rejects_wrong_hash():
    with pytest.raises(UpstreamError, match="transaction_mismatch"):
        run_with_http(
            lambda req: httpx.Response(200, json={"hash": "0x" + "00" * 32}),
            lambda http: BlockscoutClient(http, "https://test.example", 3, 10).get_transaction(TX),
        )


def test_overlapping_pages_are_deduplicated():
    def handler(request):
        cursor = None if request.url.params else {"index": 1}
        return httpx.Response(200, json={"items": [{"index": 0}], "next_page_params": cursor})

    result = run_with_http(
        handler,
        lambda http: BlockscoutClient(http, "https://test.example", 3, 10).get_transaction_logs(TX),
    )
    assert result.items == [{"index": 0}]
    assert result.complete and len(result.pages) == 2


def test_legacy_receipt_status_is_parsed_separately():
    def handler(request):
        assert request.url.params["action"] == "gettxreceiptstatus"
        return httpx.Response(200, json={"status": "1", "result": {"status": "0"}})

    assert run_with_http(
        handler,
        lambda http: BlockscoutClient(http, "https://test.example", 3, 10).get_receipt_status(TX),
    ) == {"status": "0"}


def blockscout(http, max_contract_bytes=None):
    return BlockscoutClient(http, "https://test.example/custom", 3, 10, max_contract_bytes)


def test_contract_metadata_and_abi_come_from_one_v2_request():
    implementation = "0x" + "34" * 20

    def handler(request):
        assert request.url.path == f"/custom/api/v2/smart-contracts/{ADDRESS}"
        return httpx.Response(
            200,
            json={
                "name": "Token",
                "is_verified": True,
                "proxy_type": "eip1967",
                "source_code": "contract Token {}",
                "abi": [{"type": "event"}],
                "implementations": [
                    {"address_hash": implementation.upper().replace("0X", "0x")},
                    {"address_hash": implementation},
                    {"address_hash": ADDRESS},
                    {"address_hash": "invalid"},
                    "invalid",
                ],
            },
        )

    contract = run_with_http(handler, lambda http: blockscout(http).get_contract(ADDRESS))
    assert contract.name == "Token" and contract.is_verified is True
    assert contract.implementations == [implementation]
    assert contract.abi == [{"type": "event"}]
    assert "abi" not in contract.model_dump()


def test_contract_response_uses_its_own_size_limit():
    body = {"abi": [{"type": "event"}], "source_code": "x" * 6000}

    def operation(limit):
        return lambda http: blockscout(http, limit).get_contract(ADDRESS)

    with pytest.raises(UpstreamError, match="payload_limit"):
        run_with_http(lambda req: httpx.Response(200, json=body), operation(None))
    assert run_with_http(lambda req: httpx.Response(200, json=body), operation(10000)).abi


@pytest.mark.parametrize(
    "body,code",
    [
        ({"is_verified": False}, "abi_unavailable"),
        ({"abi": []}, "abi_unavailable"),
        ({"abi": {"type": "function"}}, "invalid_abi"),
        ({"abi": [1]}, "invalid_abi"),
        ([], "invalid_contract"),
    ],
)
def test_contract_abi_rejects_unverified_or_malformed(body, code):
    with pytest.raises(UpstreamError, match=code):
        run_with_http(
            lambda req: httpx.Response(200, json=body),
            lambda http: blockscout(http).get_contract_abi(ADDRESS),
        )


def test_rpc_read_methods_and_explicit_block_reference():
    methods = []

    def handler(request):
        body = json.loads(request.content)
        methods.append(body["method"])
        values = {
            "eth_chainId": "0x141",
            "eth_getBalance": "0xffffffffffffffff",
            "eth_getCode": "0x6000",
            "eth_call": "0x01",
            "eth_getBlockByNumber": {"number": "0x20"},
            "eth_getTransactionByHash": {"hash": TX},
            "eth_getTransactionReceipt": None,
        }
        if body["method"] in {"eth_getBalance", "eth_getCode", "eth_call"}:
            assert body["params"][1] == "0x20"
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": values[body["method"]]}
        )

    async def operation(http):
        rpc = RpcClient(http, "https://rpc.example/secret")
        assert await rpc.get_chain_id() == 321
        assert await rpc.get_balance(ADDRESS, "0x20") == 2**64 - 1
        assert await rpc.get_code(ADDRESS, "0x20") == "0x6000"
        assert await rpc.read_contract(ADDRESS, "0x", "0x20") == "0x01"
        assert await rpc.get_block("0x20") == {"number": "0x20"}
        assert await rpc.get_transaction(TX) == {"hash": TX}
        assert await rpc.get_transaction_receipt(TX) is None
        with pytest.raises(ValueError):
            await rpc.call("eth_sendRawTransaction", ["0x00"])

    run_with_http(handler, operation)
    assert len(methods) == 7


@pytest.mark.parametrize(
    "response",
    [
        {"jsonrpc": "2.0", "id": 999, "result": "0x1"},
        {"jsonrpc": "2.0", "id": 1, "error": {"message": "secret-api-key"}},
        {"jsonrpc": "2.0", "id": 1},
        [],
    ],
)
def test_rpc_rejects_bad_envelopes_without_leaking(response):
    with pytest.raises(UpstreamError) as error:
        run_with_http(
            lambda req: httpx.Response(200, json=response),
            lambda http: RpcClient(http, "https://rpc.example/secret-api-key").get_chain_id(),
        )
    assert "secret" not in str(error.value)
