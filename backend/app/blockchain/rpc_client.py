from dataclasses import dataclass
from itertools import count

from pydantic import JsonValue

from app.blockchain.values import hex_data, quantity, require_address, require_hash
from app.services.http import JsonHttpClient, UpstreamError


@dataclass(frozen=True)
class CallOutcome:
    """Result of a read-only call that is allowed to revert."""

    reverted: bool
    # Return data on success; revert data (possibly "0x") when the node provides it.
    data: str | None


HISTORICAL_HINTS = (
    "archive",
    "missing trie node",
    "historical state",
    "pruned",
    "header not found",
)


class RpcClient:
    READ_METHODS = frozenset(
        {
            "eth_chainId",
            "eth_call",
            "eth_getTransactionByHash",
            "eth_getTransactionReceipt",
            "eth_getCode",
            "eth_getBalance",
            "eth_getBlockByNumber",
        }
    )

    def __init__(self, http: JsonHttpClient, url: str):
        self.http = http
        self._url = url
        self._ids = count(1)

    async def call(self, method: str, params: list[JsonValue]) -> JsonValue:
        data = await self._envelope(method, params)
        if "error" in data:
            # Provider error strings can echo API keys and endpoints.
            raise UpstreamError("rpc_error")
        return data["result"]

    async def _envelope(self, method: str, params: list[JsonValue]) -> dict:
        if method not in self.READ_METHODS:
            raise ValueError("RPC method is not allowed")
        call_id = next(self._ids)
        data = await self.http.request(
            "POST",
            self._url,
            tool=method,
            body={
                "jsonrpc": "2.0",
                "id": call_id,
                "method": method,
                "params": params,
            },
        )
        if (
            not isinstance(data, dict)
            or data.get("jsonrpc") != "2.0"
            or type(data.get("id")) is not int
            or data.get("id") != call_id
        ):
            raise UpstreamError("invalid_rpc_response")
        if "error" not in data and "result" not in data:
            raise UpstreamError("invalid_rpc_response")
        return data

    async def get_chain_id(self) -> int:
        value = quantity(await self.call("eth_chainId", []))
        if value is None:
            raise UpstreamError("invalid_rpc_response")
        return value

    async def _transaction(self, method: str, tx_hash: str, hash_key: str) -> dict | None:
        tx_hash = require_hash(tx_hash)
        data = await self.call(method, [tx_hash])
        if data is None:
            return None
        if not isinstance(data, dict) or hex_data(data.get(hash_key), 32) != tx_hash:
            raise UpstreamError("transaction_mismatch")
        return data

    async def get_transaction(self, tx_hash: str) -> dict | None:
        return await self._transaction("eth_getTransactionByHash", tx_hash, "hash")

    async def get_transaction_receipt(self, tx_hash: str) -> dict | None:
        return await self._transaction("eth_getTransactionReceipt", tx_hash, "transactionHash")

    @staticmethod
    def _block(block: str) -> str:
        if block in {"latest", "earliest", "pending", "safe", "finalized"}:
            return block
        if block.startswith("0x") and quantity(block) is not None:
            return block
        raise ValueError("Invalid block reference")

    async def get_balance(self, account: str, block: str) -> int:
        result = quantity(
            await self.call("eth_getBalance", [require_address(account), self._block(block)])
        )
        if result is None:
            raise UpstreamError("invalid_rpc_response")
        return result

    async def get_code(self, account: str, block: str) -> str:
        result = hex_data(
            await self.call("eth_getCode", [require_address(account), self._block(block)])
        )
        if result is None:
            raise UpstreamError("invalid_rpc_response")
        return result

    async def read_contract(self, account: str, data: str, block: str) -> str:
        calldata = hex_data(data)
        if calldata is None:
            raise ValueError("Invalid calldata")
        result = hex_data(
            await self.call(
                "eth_call",
                [
                    {"to": require_address(account), "data": calldata},
                    self._block(block),
                ],
            )
        )
        if result is None:
            raise UpstreamError("invalid_rpc_response")
        return result

    async def simulate(self, call: dict[str, str], block: str) -> CallOutcome:
        """eth_call that distinguishes a revert (with its data) from an unavailable node state.

        Only error codes and hex data are inspected; provider messages are classified by
        keyword and never returned or logged.
        """
        params = {key: value for key, value in call.items() if value is not None}
        data = await self._envelope("eth_call", [params, self._block(block)])
        if "error" not in data:
            result = hex_data(data.get("result"))
            if result is None:
                raise UpstreamError("invalid_rpc_response")
            return CallOutcome(False, result)
        error = data["error"] if isinstance(data["error"], dict) else {}
        message = str(error.get("message", "")).lower()
        revert_data = hex_data(error.get("data"))
        if (
            error.get("code") == 3
            or revert_data is not None
            or "revert" in message
            or "out of gas" in message
        ):
            return CallOutcome(True, revert_data)
        if any(hint in message for hint in HISTORICAL_HINTS):
            raise UpstreamError("historical_state_unavailable")
        raise UpstreamError("rpc_error")

    async def get_block(self, block: str) -> dict | None:
        data = await self.call("eth_getBlockByNumber", [self._block(block), False])
        if data is not None and not isinstance(data, dict):
            raise UpstreamError("invalid_rpc_response")
        return data
