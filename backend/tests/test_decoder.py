import pytest
from eth_abi import encode
from eth_hash.auto import keccak

from app.blockchain.decoder import (
    Decoded,
    DecodeMiss,
    canonical_type,
    decode_calldata,
    decode_log,
    decode_output,
    encode_call,
    event_topic,
    function_selector,
)

RECIPIENT = "0xe0fe6653425be82c3f6f5e6e9142e145771cdd26"
# Real USDT transfer calldata (examples/collection/phase2-erc20.json).
USDT_CALLDATA = (
    "0xa9059cbb000000000000000000000000e0fe6653425be82c3f6f5e6e9142e145771cdd26"
    "0000000000000000000000000000000000000000000000000000000002ee9470"
)
TRANSFER = {
    "type": "function",
    "name": "transfer",
    "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint"}],
}
TRANSFER_EVENT = {
    "type": "event",
    "name": "Transfer",
    "anonymous": False,
    "inputs": [
        {"name": "from", "type": "address", "indexed": True},
        {"name": "to", "type": "address", "indexed": True},
        {"name": "value", "type": "uint256", "indexed": False},
    ],
}
NFT_TRANSFER_EVENT = {
    **TRANSFER_EVENT,
    "inputs": [
        {"name": "from", "type": "address", "indexed": True},
        {"name": "to", "type": "address", "indexed": True},
        {"name": "tokenId", "type": "uint256", "indexed": True},
    ],
}
ORDER = {
    "type": "function",
    "name": "fill",
    "inputs": [
        {
            "name": "order",
            "type": "tuple",
            "components": [
                {"name": "maker", "type": "address"},
                {"name": "amounts", "type": "uint256[]"},
            ],
        },
        {"name": "note", "type": "string"},
        {"name": "", "type": "bytes4"},
    ],
}


def topic(value: bytes) -> str:
    return "0x" + value.hex()


def test_selector_and_topic_use_canonical_signatures():
    assert function_selector(TRANSFER) == "0xa9059cbb"
    assert event_topic(TRANSFER_EVENT) == topic(keccak(b"Transfer(address,address,uint256)"))
    assert canonical_type(ORDER["inputs"][0]) == "(address,uint256[])"


def test_real_usdt_calldata_is_decoded_with_exact_values():
    result = decode_calldata(USDT_CALLDATA, [TRANSFER_EVENT, TRANSFER])
    assert isinstance(result, Decoded)
    assert result.signature == "transfer(address,uint256)"
    assert [(a.name, a.type, a.value) for a in result.arguments] == [
        ("_to", "address", RECIPIENT),
        ("_value", "uint256", "49190000"),
    ]


def test_max_uint256_survives_as_string():
    calldata = "0xa9059cbb" + encode(["address", "uint256"], [RECIPIENT, 2**256 - 1]).hex()
    result = decode_calldata(calldata, [TRANSFER])
    assert isinstance(result, Decoded)
    assert result.arguments[1].value == str(2**256 - 1)


def test_tuple_array_string_and_unnamed_arguments():
    selector = function_selector(ORDER)
    body = encode(
        ["(address,uint256[])", "string", "bytes4"],
        [(RECIPIENT, [1, 2]), "olá", b"\x01\x02\x03\x04"],
    )
    result = decode_calldata(selector + body.hex(), [ORDER])
    assert isinstance(result, Decoded)
    order, note, unnamed = result.arguments
    assert order.value == {"maker": RECIPIENT, "amounts": ["1", "2"]}
    assert note.value == "olá"
    assert (unnamed.name, unnamed.value) == ("_2", "0x01020304")


def test_large_arrays_are_truncated_and_flagged():
    item = {"type": "function", "name": "batch", "inputs": [{"name": "ids", "type": "uint8[]"}]}
    calldata = function_selector(item) + encode(["uint8[]"], [[1] * 60]).hex()
    result = decode_calldata(calldata, [item])
    assert isinstance(result, Decoded)
    assert len(result.arguments[0].value) == 50 and result.arguments[0].truncated


@pytest.mark.parametrize(
    "calldata,abi,reason",
    [
        ("0x", [TRANSFER], "no_selector"),
        ("0x12345678", [TRANSFER], "selector_not_in_abi"),
        (USDT_CALLDATA[:40], [TRANSFER], "decode_failed"),
        (USDT_CALLDATA, [{"type": "function", "name": "transfer", "inputs": "bad"}], None),
    ],
)
def test_calldata_without_matching_abi_is_never_guessed(calldata, abi, reason):
    result = decode_calldata(calldata, abi)
    assert isinstance(result, DecodeMiss)
    assert reason is None or result.reason == reason


def test_erc20_event_is_decoded():
    data = "0x" + encode(["uint256"], [49190000]).hex()
    topics = [
        event_topic(TRANSFER_EVENT),
        "0x" + "00" * 12 + "11" * 20,
        "0x" + "00" * 12 + RECIPIENT[2:],
    ]
    result = decode_log(topics, data, [TRANSFER, TRANSFER_EVENT])
    assert isinstance(result, Decoded)
    assert [a.value for a in result.arguments] == ["0x" + "11" * 20, RECIPIENT, "49190000"]


def test_event_layout_is_selected_by_indexed_topic_count():
    topics = [
        event_topic(TRANSFER_EVENT),
        "0x" + "00" * 12 + "11" * 20,
        "0x" + "00" * 12 + RECIPIENT[2:],
        "0x" + encode(["uint256"], [7]).hex(),
    ]
    result = decode_log(topics, "0x", [TRANSFER_EVENT, NFT_TRANSFER_EVENT])
    assert isinstance(result, Decoded)
    assert result.arguments[2].name == "tokenId" and result.arguments[2].value == "7"


def test_indexed_reference_types_remain_hashes():
    item = {
        "type": "event",
        "name": "Named",
        "inputs": [{"name": "label", "type": "string", "indexed": True}],
    }
    hashed = topic(keccak(b"label"))
    result = decode_log([event_topic(item), hashed], "0x", [item])
    assert isinstance(result, Decoded)
    assert result.arguments[0].hashed and result.arguments[0].value == hashed


@pytest.mark.parametrize(
    "topics,data,reason",
    [
        ([], "0x", "anonymous_or_no_topics"),
        (["0x" + "99" * 32], "0x", "event_not_in_abi"),
        ([event_topic(TRANSFER_EVENT)], "0x", "decode_failed"),
    ],
)
def test_logs_without_matching_layout_are_misses(topics, data, reason):
    result = decode_log(topics, data, [TRANSFER_EVENT])
    assert isinstance(result, DecodeMiss) and result.reason == reason


VIEW = {
    "type": "function",
    "name": "quote",
    "stateMutability": "view",
    "inputs": [
        {"name": "owner", "type": "address"},
        {"name": "amount", "type": "uint256"},
        {"name": "flag", "type": "bool"},
        {"name": "ids", "type": "uint8[]"},
    ],
    "outputs": [{"name": "ok", "type": "bool"}, {"name": "value", "type": "uint256"}],
}


def test_encode_call_accepts_string_arguments_from_tools():
    from eth_abi import decode

    calldata = encode_call(VIEW, [RECIPIENT, "1000", "true", "[1, 2]"])
    assert calldata.startswith(function_selector(VIEW))
    owner, amount, flag, ids = decode(
        ["address", "uint256", "bool", "uint8[]"], bytes.fromhex(calldata[10:])
    )
    assert (owner, amount, flag, ids) == (RECIPIENT, 1000, True, (1, 2))


@pytest.mark.parametrize(
    "args",
    [
        [RECIPIENT],
        [RECIPIENT, "x", "true", "[]"],
        ["0x12", "1", "true", "[]"],
        [RECIPIENT, "1", "maybe", "[]"],
        [RECIPIENT, "-1", "true", "[]"],
    ],
)
def test_encode_call_rejects_bad_arguments(args):
    with pytest.raises(ValueError):
        encode_call(VIEW, args)


def test_decode_output_names_values():
    data = "0x" + encode(["bool", "uint256"], [True, 7]).hex()
    assert [(a.name, a.value) for a in decode_output(VIEW, data)] == [("ok", True), ("value", "7")]


MULTICALL = {
    "type": "function",
    "name": "multicall",
    "inputs": [{"name": "data", "type": "bytes[]"}],
}
REFUND = {"type": "function", "name": "refundETH", "inputs": []}


def multicall(*elements: bytes) -> str:
    return "0xac9650d8" + encode(["bytes[]"], [list(elements)]).hex()


def test_multicall_elements_are_decoded_with_the_same_abi():
    refund = bytes.fromhex(function_selector(REFUND)[2:])
    calldata = multicall(bytes.fromhex(USDT_CALLDATA[2:]), refund, b"\x12\x34\x56\x78\x00")
    decoded = decode_calldata(calldata, [MULTICALL, TRANSFER, REFUND])
    assert isinstance(decoded, Decoded)
    assert [(n.index, n.call.signature if n.call else n.reason) for n in decoded.nested] == [
        (0, "transfer(address,uint256)"),
        (1, "refundETH()"),
        (2, "selector_not_in_abi"),
    ]
    assert decoded.nested[0].call.arguments[1].value == "49190000"
    assert decoded.nested[0].argument == "data"


def test_bytes_array_without_calls_is_not_treated_as_multicall():
    calldata = multicall(b"\xaa" * 65, b"\xbb" * 65)
    decoded = decode_calldata(calldata, [MULTICALL, TRANSFER])
    assert isinstance(decoded, Decoded) and decoded.nested == []
