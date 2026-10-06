from app.blockchain.values import address, hex_data, quantity, units
from app.domain.transaction import IndexedTransfer, InternalCall, RawLog, TransactionDetails


def numeric(value: object) -> str | None:
    number = quantity(value)
    return str(number) if number is not None else None


def transaction_fields(data: dict, kind: str) -> dict:
    explorer = kind == "explorer"
    return {
        "sender": address(data.get("from")),
        "recipient": address(data.get("to")),
        # Explorer-only: whether the recipient has code. RPC transactions do not say.
        "recipient_is_contract": (
            data["to"].get("is_contract")
            if explorer
            and isinstance(data.get("to"), dict)
            and isinstance(data["to"].get("is_contract"), bool)
            else None
        ),
        "created_contract": address(
            data.get("created_contract" if explorer else "contractAddress")
        ),
        "block_number": quantity(data.get("block_number" if explorer else "blockNumber")),
        "block_hash": hex_data(data.get("block_hash" if explorer else "blockHash"), 32),
        "value_raw": numeric(data.get("value")),
        "calldata": hex_data(data.get("raw_input" if explorer else "input")),
        "gas_limit": numeric(data.get("gas_limit" if explorer else "gas")),
        "gas_used": numeric(data.get("gas_used" if explorer else "gasUsed")),
        "gas_price": numeric(data.get("gas_price" if explorer else "gasPrice")),
    }


def status_of(data: dict, kind: str) -> str:
    if kind == "explorer":
        if data.get("status") == "ok":
            return "success"
        if data.get("status") == "error":
            return "failed"
        if data.get("result") == "pending":
            return "pending"
    elif kind == "receipt":
        status = quantity(data.get("status"))
        if status in (0, 1) and quantity(data.get("blockNumber")) is not None:
            return "success" if status == 1 else "failed"
    elif "blockNumber" in data and data["blockNumber"] is None:
        return "pending"
    return "unknown"


def merge_transaction(
    inputs: list[tuple[str, str, dict]],
    currency: str,
    decimals: int,
) -> tuple[TransactionDetails, str, list[str], list[str]]:
    fields: dict = {}
    sources: dict[str, list[str]] = {}
    conflicts: set[str] = set()
    states: dict[str, list[str]] = {}
    issues: list[str] = []
    for source_id, kind, data in inputs:
        state = status_of(data, kind)
        if state != "unknown":
            states.setdefault(state, []).append(source_id)
        for key, value in transaction_fields(data, kind).items():
            if value is None:
                continue
            sources.setdefault(key, []).append(source_id)
            if key in fields and fields[key] != value:
                conflicts.add(key)
            else:
                fields[key] = value
    for key in sorted(conflicts):
        fields.pop(key, None)
        sources.pop(key, None)
        issues.append(f"Fontes divergentes para {key}; campo omitido. Confira o explorer e o RPC.")
    state = next(iter(states)) if len(states) == 1 else "unknown"
    if len(states) > 1 or "block_number" in conflicts or "block_hash" in conflicts:
        state = "unknown"
        issues.append(
            "Status/bloco divergente entre fontes. Refaça a consulta para confirmar a inclusão."
        )
    status_sources = [item for ids in states.values() for item in ids]
    transaction = TransactionDetails(native_currency=currency, field_sources=sources, **fields)
    if transaction.value_raw is not None:
        transaction.value_display = units(int(transaction.value_raw), decimals)
        sources["value_display"] = sources["value_raw"]
    if transaction.calldata and len(transaction.calldata) >= 10:
        transaction.selector = transaction.calldata[:10]
        sources["selector"] = sources["calldata"]
    transaction.field_sources = sources
    return transaction, state, status_sources, issues


def normalize_logs(items: list[dict], source_id: str, tx_hash: str) -> tuple[list[RawLog], int]:
    logs: list[RawLog] = []
    rejected = 0
    seen: set[tuple] = set()
    for item in items:
        item_hash = item.get("transaction_hash", item.get("transactionHash"))
        account = address(item.get("address", item.get("address_hash")))
        data = hex_data(item.get("data"))
        topics = item.get("topics")
        if (
            item.get("removed") is True
            or (item_hash is not None and hex_data(item_hash, 32) != tx_hash)
            or account is None
            or data is None
            or not isinstance(topics, list)
            or len(topics) > 4
            or any(t is not None and hex_data(t, 32) is None for t in topics)
        ):
            rejected += 1
            continue
        topic_values = [hex_data(t, 32) for t in topics if t is not None]
        index = quantity(item.get("index", item.get("logIndex")))
        key = (index, account, data, tuple(topic_values))
        if index is not None and key in seen:
            continue
        seen.add(key)
        logs.append(
            RawLog(
                address=account,
                index=index,
                topics=topic_values,
                data=data,
                evidence_ids=[source_id],
            )
        )
    return logs, rejected


def normalize_transfer(item: dict, source_id: str) -> IndexedTransfer:
    token = item.get("token") if isinstance(item.get("token"), dict) else {}
    return IndexedTransfer(
        sender=address(item.get("from")),
        recipient=address(item.get("to")),
        token_address=address(token.get("address_hash", token.get("address"))),
        token_symbol=token.get("symbol") if isinstance(token.get("symbol"), str) else None,
        token_type=token.get("type") if isinstance(token.get("type"), str) else None,
        total=item.get("total"),
        evidence_ids=[source_id],
    )


def normalize_call(item: dict, source_id: str) -> InternalCall:
    return InternalCall(
        sender=address(item.get("from")),
        recipient=address(item.get("to")),
        call_type=item.get("type") if isinstance(item.get("type"), str) else None,
        success=item.get("success") if isinstance(item.get("success"), bool) else None,
        value_raw=numeric(item.get("value")),
        evidence_ids=[source_id],
    )
