import asyncio
import json
from typing import Literal

import httpx
from pydantic import BaseModel, Field, JsonValue

from app.blockchain.values import address, hex_data, require_address, require_hash
from app.services.http import JsonHttpClient, UpstreamError


class Collection(BaseModel):
    items: list[dict[str, JsonValue]] = Field(default_factory=list)
    pages: list[str] = Field(default_factory=list)
    complete: bool = False
    issue: str | None = None


def flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def verified_sources(data: dict) -> dict[str, str]:
    """Verified source files keyed by path: the main file plus additional sources."""
    extra = data.get("additional_sources")
    files = [data, *(extra if isinstance(extra, list) else [])]
    sources = {}
    for item in files:
        if (
            isinstance(item, dict)
            and isinstance(item.get("file_path"), str)
            and isinstance(item.get("source_code"), str)
        ):
            sources[item["file_path"]] = item["source_code"]
    return sources


class ContractMetadata(BaseModel):
    address: str
    name: str | None = None
    is_verified: bool | None = None
    proxy_type: str | None = None
    implementations: list[str] = Field(default_factory=list)
    # Excluded from evidence payloads; ABI and source are summarized where they are cited.
    abi: list[dict] | None = Field(default=None, exclude=True)
    sources: dict[str, str] = Field(default_factory=dict, exclude=True)


class BlockscoutClient:
    def __init__(
        self,
        http: JsonHttpClient,
        base_url: str,
        max_pages: int,
        max_items: int,
        max_contract_bytes: int | None = None,
    ):
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.max_pages = max_pages
        self.max_items = max_items
        self.max_contract_bytes = max_contract_bytes

    def get_explorer_transaction_url(self, tx_hash: str) -> str:
        return f"{self.base_url}/tx/{require_hash(tx_hash)}"

    def transaction_api_url(self, tx_hash: str) -> str:
        return f"{self.base_url}/api/v2/transactions/{require_hash(tx_hash)}"

    def get_explorer_address_url(self, account: str) -> str:
        return f"{self.base_url}/address/{require_address(account)}"

    def get_explorer_contract_url(self, account: str) -> str:
        return f"{self.get_explorer_address_url(account)}?tab=contract"

    def contract_api_url(self, account: str) -> str:
        return f"{self.base_url}/api/v2/smart-contracts/{require_address(account)}"

    async def ping(self) -> None:
        """Connectivity check used by the settings screen (small, cacheable stats endpoint)."""
        data = await self.http.request(
            "GET", f"{self.base_url}/api/v2/stats", tool="blockscout.stats"
        )
        if not isinstance(data, dict):
            raise UpstreamError("invalid_explorer_response")

    async def get_contract(self, account: str) -> ContractMetadata:
        """Verification metadata, proxy implementations and ABI from one v2 request.

        The response also carries verified source code, so it has its own size limit.
        """
        data = await self.http.request(
            "GET",
            self.contract_api_url(account),
            tool="blockscout.smart_contract",
            max_bytes=self.max_contract_bytes,
        )
        if not isinstance(data, dict):
            raise UpstreamError("invalid_contract")
        implementations = data.get("implementations")
        abi = data.get("abi")
        if abi is not None and (
            not isinstance(abi, list) or not all(isinstance(item, dict) for item in abi)
        ):
            raise UpstreamError("invalid_abi")
        return ContractMetadata(
            address=require_address(account),
            name=data["name"] if isinstance(data.get("name"), str) else None,
            is_verified=flag(data.get("is_verified")),
            proxy_type=data["proxy_type"] if isinstance(data.get("proxy_type"), str) else None,
            implementations=list(
                dict.fromkeys(
                    impl
                    for item in (implementations if isinstance(implementations, list) else [])
                    if isinstance(item, dict)
                    and (impl := address(item.get("address_hash")))
                    and impl != require_address(account)
                )
            ),
            abi=abi or None,
            sources=verified_sources(data),
        )

    async def get_contract_abi(self, account: str) -> list[dict]:
        contract = await self.get_contract(account)
        if not contract.abi:
            raise UpstreamError("abi_unavailable")
        return contract.abi

    async def get_transaction(self, tx_hash: str) -> dict:
        data = await self.http.request(
            "GET", self.transaction_api_url(tx_hash), tool="blockscout.transaction"
        )
        if not isinstance(data, dict) or hex_data(data.get("hash"), 32) != require_hash(tx_hash):
            raise UpstreamError("transaction_mismatch")
        return data

    async def get_receipt_status(self, tx_hash: str) -> dict:
        # This legacy endpoint returns receipt status, NOT a full JSON-RPC receipt.
        data = await self.http.request(
            "GET",
            f"{self.base_url}/api",
            tool="blockscout.receipt_status",
            params={
                "module": "transaction",
                "action": "gettxreceiptstatus",
                "txhash": require_hash(tx_hash),
            },
        )
        if (
            not isinstance(data, dict)
            or data.get("status") != "1"
            or not isinstance(data.get("result"), dict)
            or data["result"].get("status") not in ("0", "1")
        ):
            raise UpstreamError("invalid_receipt_status")
        return data["result"]

    async def get_collection(
        self,
        tx_hash: str,
        kind: Literal["logs", "token-transfers", "internal-transactions"],
    ) -> Collection:
        path = f"{self.transaction_api_url(tx_hash)}/{kind}"
        result = Collection()
        params: dict[str, str] = {}
        seen: set[str] = set()
        seen_items: set[str] = set()
        for _ in range(self.max_pages):
            try:
                data = await self.http.request(
                    "GET", path, tool=f"blockscout.{kind}", params=params
                )
                if (
                    not isinstance(data, dict)
                    or not isinstance(data.get("items"), list)
                    or not all(isinstance(item, dict) for item in data["items"])
                ):
                    raise UpstreamError("invalid_collection")
            except asyncio.CancelledError:
                result.issue = "timeout"
                return result
            except UpstreamError as exc:
                result.issue = exc.code
                return result
            result.pages.append(str(httpx.URL(path, params=params)))
            fresh = []
            for item in data["items"]:
                # Identical records on overlapping pages count only once. Indexed
                # events remain distinct because their log/call index differs.
                item_key = json.dumps(item, sort_keys=True)
                if item_key not in seen_items:
                    fresh.append(item)
                    seen_items.add(item_key)
            remaining = self.max_items - len(result.items)
            result.items.extend(fresh[:remaining])
            cursor = data.get("next_page_params")
            if len(fresh) > remaining:
                result.issue = "collection_limit"
                return result
            if "next_page_params" not in data:
                result.issue = "missing_pagination"
                return result
            if cursor is None:
                result.complete = True
                return result
            if len(result.items) >= self.max_items:
                result.issue = "collection_limit"
                return result
            if (
                not isinstance(cursor, dict)
                or not cursor
                or not all(
                    isinstance(v, (str, int)) and not isinstance(v, bool) for v in cursor.values()
                )
            ):
                result.issue = "invalid_pagination"
                return result
            key = json.dumps(cursor, sort_keys=True)
            if key in seen:
                result.issue = "repeated_pagination"
                return result
            seen.add(key)
            # The server can only provide query parameters, never a new host/path.
            params = {k: str(v) for k, v in cursor.items()}
        result.issue = "page_limit"
        return result

    async def get_transaction_logs(self, tx_hash: str) -> Collection:
        return await self.get_collection(tx_hash, "logs")

    async def get_token_transfers(self, tx_hash: str) -> Collection:
        return await self.get_collection(tx_hash, "token-transfers")

    async def get_internal_transactions(self, tx_hash: str) -> Collection:
        return await self.get_collection(tx_hash, "internal-transactions")
