"""Deterministic ABI decoding. Pure functions: no I/O and no guessing without an ABI."""

import json
import re
from dataclasses import dataclass, field

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_hash.auto import keccak
from pydantic import JsonValue

from app.blockchain.values import hex_data
from app.domain.transaction import DecodedArg

MAX_ITEMS = 50
MAX_TEXT = 2000
MAX_DEPTH = 8
# Elements of a bytes[] argument decoded as calls to the same contract (multicall pattern).
MAX_NESTED = 10
# Sizes are validated again by eth-abi when values are decoded.
ELEMENTARY = re.compile(r"^(u?int\d{0,3}|address|bool|string|bytes\d{0,2}|function)$")
ARRAY_SUFFIX = re.compile(r"^(\[\d*\])*$")


class AbiFormatError(ValueError):
    pass


@dataclass
class Decoded:
    name: str
    signature: str
    arguments: list[DecodedArg]
    nested: list["Nested"] = field(default_factory=list)


@dataclass
class Nested:
    """One bytes[] element read as a call to the same ABI; `call` is None on a miss."""

    argument: str
    index: int
    selector: str | None
    call: "Decoded | None"
    reason: str | None = None


@dataclass
class DecodeMiss:
    reason: str


def canonical_type(param: object, depth: int = 0) -> str:
    if not isinstance(param, dict) or not isinstance(param.get("type"), str) or depth > MAX_DEPTH:
        raise AbiFormatError("invalid_param")
    kind = param["type"]
    if kind.startswith("tuple"):
        suffix = kind[5:]
        components = param.get("components")
        if not isinstance(components, list) or not ARRAY_SUFFIX.fullmatch(suffix):
            raise AbiFormatError("invalid_tuple")
        return "(" + ",".join(canonical_type(c, depth + 1) for c in components) + ")" + suffix
    base = kind.split("[", 1)[0]
    if not ELEMENTARY.fullmatch(base) or not ARRAY_SUFFIX.fullmatch(kind[len(base) :]):
        raise AbiFormatError("invalid_type")
    # Solidity aliases must be canonicalized before hashing a signature.
    if base in ("uint", "int"):
        kind = base + "256" + kind[len(base) :]
    return kind


def _inputs(item: dict) -> list[dict]:
    inputs = item.get("inputs", [])
    if not isinstance(inputs, list):
        raise AbiFormatError("invalid_inputs")
    return inputs


def signature(item: dict) -> str:
    name = item.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name):
        raise AbiFormatError("invalid_name")
    return f"{name}({','.join(canonical_type(p) for p in _inputs(item))})"


def function_selector(item: dict) -> str:
    return "0x" + keccak(signature(item).encode())[:4].hex()


def event_topic(item: dict) -> str:
    return "0x" + keccak(signature(item).encode()).hex()


def _is_reference(param: dict) -> bool:
    kind = param["type"]
    return kind in ("string", "bytes") or kind.startswith("tuple") or kind.endswith("]")


def serialize(value: object, param: dict, depth: int = 0) -> tuple[JsonValue, bool]:
    """Convert eth-abi values to JSON without losing precision; returns (value, truncated)."""
    kind = param["type"]
    if depth > MAX_DEPTH:
        return None, True
    if kind.endswith("]"):
        element = {**param, "type": kind[: kind.rindex("[")]}
        items = list(value)  # type: ignore[call-overload]
        out: list[JsonValue] = []
        truncated = len(items) > MAX_ITEMS
        for item in items[:MAX_ITEMS]:
            converted, cut = serialize(item, element, depth + 1)
            out.append(converted)
            truncated = truncated or cut
        return out, truncated
    if kind == "tuple":
        result: dict[str, JsonValue] = {}
        truncated = False
        for index, (component, item) in enumerate(zip(param["components"], value, strict=True)):
            key = component.get("name") or f"_{index}"
            result[key], cut = serialize(item, component, depth + 1)
            truncated = truncated or cut
        return result, truncated
    if isinstance(value, bool):
        return value, False
    if isinstance(value, int):
        return str(value), False
    if isinstance(value, bytes):
        text = "0x" + value.hex()
        return (text[:MAX_TEXT], True) if len(text) > MAX_TEXT else (text, False)
    if isinstance(value, str):
        if kind == "address":
            return value.lower(), False
        return (value[:MAX_TEXT], True) if len(value) > MAX_TEXT else (value, False)
    return str(value), False


def _arguments(params: list[dict], values: tuple) -> list[DecodedArg]:
    args = []
    for index, (param, value) in enumerate(zip(params, values, strict=True)):
        converted, truncated = serialize(value, param)
        args.append(
            DecodedArg(
                name=param.get("name") or f"_{index}",
                type=canonical_type(param),
                value=converted,
                truncated=truncated,
            )
        )
    return args


def _nested_calls(params: list[dict], values: tuple, abi: list[dict]) -> list[Nested]:
    """multicall(bytes[]) delegatecalls itself: each element is calldata for the same ABI.

    Only bytes[] arguments are tried, one level deep, and only strict decodes count, so an
    element that merely starts with a known selector is reported as a miss, not a call."""
    nested: list[Nested] = []
    for index, (param, value) in enumerate(zip(params, values, strict=True)):
        if canonical_type(param) != "bytes[]":
            continue
        name = param.get("name") or f"_{index}"
        for position, item in enumerate(list(value)[:MAX_NESTED]):
            data = "0x" + bytes(item).hex()
            selector = data[:10] if len(data) >= 10 else None
            outcome = decode_calldata(data, abi, nested=False)
            if isinstance(outcome, Decoded):
                nested.append(Nested(name, position, selector, outcome))
            else:
                nested.append(Nested(name, position, selector, None, outcome.reason))
    # A bytes[] that holds no calls to this ABI (e.g. signatures) is not a multicall.
    return nested if any(n.call for n in nested) else []


def decode_calldata(
    calldata: str | None, abi: list[dict], *, nested: bool = True
) -> Decoded | DecodeMiss:
    data = hex_data(calldata)
    if data is None or len(data) < 10:
        return DecodeMiss("no_selector")
    selector = data[:10]
    matches = []
    for item in abi:
        if item.get("type", "function") != "function":
            continue
        try:
            if function_selector(item) == selector:
                matches.append(item)
        except AbiFormatError:
            continue
    signatures = {signature(item) for item in matches}
    if not matches:
        return DecodeMiss("selector_not_in_abi")
    if len(signatures) > 1:
        return DecodeMiss("ambiguous_selector")
    item = matches[0]
    params = _inputs(item)
    try:
        values = abi_decode(
            [canonical_type(p) for p in params], bytes.fromhex(data[10:]), strict=True
        )
        arguments = _arguments(params, values)
    except Exception:  # eth-abi raises several unrelated types for malformed data.
        return DecodeMiss("decode_failed")
    inner = _nested_calls(params, values, abi) if nested else []
    return Decoded(name=item["name"], signature=signature(item), arguments=arguments, nested=inner)


def decode_log(topics: list[str], data: str, abi: list[dict]) -> Decoded | DecodeMiss:
    if not topics:
        return DecodeMiss("anonymous_or_no_topics")
    candidates = []
    for item in abi:
        if item.get("type") != "event" or item.get("anonymous") is True:
            continue
        try:
            if event_topic(item) == topics[0]:
                candidates.append(item)
        except AbiFormatError:
            continue
    if not candidates:
        return DecodeMiss("event_not_in_abi")
    for item in candidates:
        # Same topic0 can describe different layouts (ERC-20 vs ERC-721 Transfer).
        params = _inputs(item)
        indexed = [p for p in params if p.get("indexed") is True]
        if len(indexed) != len(topics) - 1:
            continue
        try:
            plain = [p for p in params if p.get("indexed") is not True]
            plain_values = iter(
                abi_decode([canonical_type(p) for p in plain], bytes.fromhex(data[2:]), strict=True)
            )
            topic_values = iter(topics[1:])
            arguments = []
            for index, param in enumerate(params):
                name = param.get("name") or f"_{index}"
                kind = canonical_type(param)
                if param.get("indexed") is not True:
                    value, truncated = serialize(next(plain_values), param)
                    arguments.append(
                        DecodedArg(name=name, type=kind, value=value, truncated=truncated)
                    )
                elif _is_reference(param):
                    arguments.append(
                        DecodedArg(name=name, type=kind, value=next(topic_values), hashed=True)
                    )
                else:
                    raw = bytes.fromhex(next(topic_values)[2:])
                    value, truncated = serialize(abi_decode([kind], raw, strict=True)[0], param)
                    arguments.append(
                        DecodedArg(name=name, type=kind, value=value, truncated=truncated)
                    )
        except Exception:  # eth-abi raises several unrelated types for malformed data.
            continue
        return Decoded(name=item["name"], signature=signature(item), arguments=arguments)
    return DecodeMiss("decode_failed")


def _coerce(value: object, kind: str, components: list | None = None) -> object:
    """Convert JSON arguments (from the agent) into eth-abi values; raises ValueError."""
    if isinstance(value, str) and (kind.endswith("]") or kind.startswith("(")):
        # Tool arguments arrive as strings; composite values are JSON-encoded.
        value = json.loads(value)
    if kind == "bool" and value in ("true", "false"):
        value = value == "true"
    if kind.endswith("]"):
        if not isinstance(value, list):
            raise ValueError("array expected")
        element = kind[: kind.rindex("[")]
        return [_coerce(item, element, components) for item in value]
    if kind.startswith("("):
        params = components or []
        items = [value.get(p.get("name")) for p in params] if isinstance(value, dict) else value
        if not isinstance(items, list) or len(items) != len(params):
            raise ValueError("tuple expected")
        return tuple(
            _coerce(item, canonical_type(p), p.get("components"))
            for item, p in zip(items, params, strict=True)
        )
    if kind.startswith(("uint", "int")):
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError("integer expected")
        return value if isinstance(value, int) else int(value, 0)
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError("boolean expected")
        return value
    if kind == "address":
        account = hex_data(value, 20)
        if account is None:
            raise ValueError("address expected")
        return account
    if kind.startswith("bytes"):
        data = hex_data(value)
        if data is None:
            raise ValueError("hex bytes expected")
        return bytes.fromhex(data[2:])
    if kind == "string":
        if not isinstance(value, str):
            raise ValueError("string expected")
        return value
    raise ValueError("unsupported type")


def encode_call(item: dict, args: list) -> str:
    """Calldata for a read-only call; raises ValueError for unusable arguments."""

    params = _inputs(item)
    if not isinstance(args, list) or len(args) != len(params):
        raise ValueError("argument count mismatch")
    types_ = [canonical_type(p) for p in params]
    values = [
        _coerce(arg, kind, p.get("components"))
        for arg, kind, p in zip(args, types_, params, strict=True)
    ]
    try:
        body = abi_encode(types_, values)
    except Exception as exc:  # eth-abi raises several types for out-of-range values.
        raise ValueError("encoding failed") from exc
    return function_selector(item) + body.hex()


def decode_output(item: dict, data: str) -> list[DecodedArg]:
    outputs = item.get("outputs", [])
    if not isinstance(outputs, list):
        raise AbiFormatError("invalid_outputs")
    raw = hex_data(data)
    if raw is None:
        raise ValueError("invalid return data")
    values = abi_decode([canonical_type(p) for p in outputs], bytes.fromhex(raw[2:]), strict=True)
    return _arguments(outputs, values)


ERROR_STRING = "0x08c379a0"  # Error(string): require/revert with a message
PANIC = "0x4e487b71"  # Panic(uint256): compiler-inserted checks (Solidity >= 0.8)
PANIC_CODES = {
    0x00: "erro genérico do compilador",
    0x01: "assert falhou",
    0x11: "overflow ou underflow aritmético",
    0x12: "divisão ou módulo por zero",
    0x21: "conversão para enum inválida",
    0x22: "array de armazenamento codificado incorretamente",
    0x31: "pop em array vazio",
    0x32: "acesso a índice fora dos limites do array",
    0x41: "memória insuficiente ou array grande demais",
    0x51: "chamada a função interna não inicializada",
}


@dataclass
class DecodedRevert:
    kind: str  # error_string | panic | custom_error | empty | unknown
    signature: str | None = None
    name: str | None = None
    arguments: list[DecodedArg] | None = None
    message: str | None = None
    # Address whose ABI declared the custom error.
    abi_address: str | None = None
    # Other ABIs declaring a different error with the same selector.
    ambiguous: bool = False


def error_selector(item: dict) -> str:
    return "0x" + keccak(signature(item).encode())[:4].hex()


def decode_revert(data: str | None, abis: list[tuple[str, list[dict]]]) -> DecodedRevert:
    """Decode revert bytes: Error(string), Panic(uint256) or a custom error from known ABIs."""
    raw = hex_data(data)
    if raw is None or raw == "0x":
        # Bare revert(), require without message, invalid opcode or out of gas.
        return DecodedRevert("empty")
    if len(raw) < 10:
        return DecodedRevert("unknown")
    selector, body = raw[:10], bytes.fromhex(raw[10:])
    try:
        if selector == ERROR_STRING:
            (message,) = abi_decode(["string"], body)
            text, _ = serialize(message, {"type": "string"})
            return DecodedRevert("error_string", "Error(string)", "Error", message=str(text))
        if selector == PANIC:
            (code,) = abi_decode(["uint256"], body)
            meaning = PANIC_CODES.get(code, "código de pânico desconhecido")
            return DecodedRevert(
                "panic", "Panic(uint256)", "Panic", message=f"0x{code:02x}: {meaning}"
            )
    except Exception:  # Malformed standard payload: keep the raw bytes only.
        return DecodedRevert("unknown")
    matches: list[tuple[str, dict]] = []
    for address, abi in abis:
        for item in abi:
            if item.get("type") != "error":
                continue
            try:
                if error_selector(item) == selector:
                    matches.append((address, item))
            except AbiFormatError:
                continue
    for address, item in matches:
        params = _inputs(item)
        try:
            values = abi_decode([canonical_type(p) for p in params], body, strict=True)
            arguments = _arguments(params, values)
        except Exception:
            continue
        signatures = {signature(other) for _, other in matches}
        return DecodedRevert(
            "custom_error",
            signature(item),
            item["name"],
            arguments,
            abi_address=address,
            ambiguous=len(signatures) > 1,
        )
    return DecodedRevert("unknown")
