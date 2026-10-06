from typing import Literal

from pydantic import BaseModel, Field, JsonValue


class DecodedArg(BaseModel):
    name: str
    type: str
    # Integers are decimal strings and bytes are hex, so uint256 survives JSON clients.
    value: JsonValue
    # Indexed reference types are stored by the EVM only as a keccak hash.
    hashed: bool = False
    truncated: bool = False


class NestedCall(BaseModel):
    """A bytes[] element of the top-level call decoded with the same ABI (multicall)."""

    argument: str
    index: int
    selector: str | None = None
    # None when the element did not decode strictly; `reason` then says why.
    function: str | None = None
    signature: str | None = None
    arguments: list[DecodedArg] = []
    reason: str | None = None


class DecodedCall(BaseModel):
    function: str
    signature: str
    selector: str
    arguments: list[DecodedArg]
    abi_source: Literal["explorer", "repository"]
    contract: str
    # ABI owner when the call reached a proxy and matched its implementation ABI.
    abi_address: str
    evidence_ids: list[str]
    nested_calls: list[NestedCall] = []


class DecodedEvent(BaseModel):
    name: str
    signature: str
    arguments: list[DecodedArg]
    abi_source: Literal["explorer", "repository"]
    abi_address: str
    evidence_ids: list[str]


class TransactionDetails(BaseModel):
    sender: str | None = None
    recipient: str | None = None
    recipient_is_contract: bool | None = None
    created_contract: str | None = None
    block_number: int | None = None
    block_hash: str | None = None
    value_raw: str | None = None
    value_display: str | None = None
    native_currency: str
    calldata: str | None = None
    selector: str | None = None
    gas_limit: str | None = None
    gas_used: str | None = None
    gas_price: str | None = None
    decoded_input: DecodedCall | None = None
    field_sources: dict[str, list[str]] = Field(default_factory=dict)


class RawLog(BaseModel):
    address: str
    index: int | None = None
    topics: list[str]
    data: str
    decoded: DecodedEvent | None = None
    evidence_ids: list[str]


class IndexedTransfer(BaseModel):
    sender: str | None = None
    recipient: str | None = None
    token_address: str | None = None
    token_symbol: str | None = None
    token_type: str | None = None
    # Preserve raw totals, including NFT/batch shapes; do not guess decimals.
    total: JsonValue = None
    evidence_ids: list[str]


class InternalCall(BaseModel):
    sender: str | None = None
    recipient: str | None = None
    call_type: str | None = None
    success: bool | None = None
    value_raw: str | None = None
    evidence_ids: list[str]
