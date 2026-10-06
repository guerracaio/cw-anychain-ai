import re


def quantity(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value < 2**256 else None
    if not isinstance(value, str) or len(value) > 80:
        return None
    if re.fullmatch(r"0x[0-9a-fA-F]+", value):
        number = int(value, 16)
    elif re.fullmatch(r"[0-9]+", value):
        number = int(value)
    else:
        return None
    return number if number < 2**256 else None


def hex_data(value: object, size: int | None = None) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", value):
        return None
    if size is not None and len(value) != 2 + size * 2:
        return None
    return value.lower()


def address(value: object) -> str | None:
    return hex_data(value.get("hash") if isinstance(value, dict) else value, 20)


def units(value: int, decimals: int) -> str:
    # String arithmetic preserves every digit, even for uint256 and unusual decimals.
    if decimals == 0:
        return str(value)
    padded = str(value).zfill(decimals + 1)
    return (padded[:-decimals] + "." + padded[-decimals:]).rstrip("0").rstrip(".")


def require_hash(value: str) -> str:
    result = hex_data(value, 32)
    if result is None:
        raise ValueError("Invalid transaction hash")
    return result


def require_address(value: str) -> str:
    result = address(value)
    if result is None:
        raise ValueError("Invalid address")
    return result
