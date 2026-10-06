import pytest

from app.blockchain.normalize import merge_transaction, normalize_logs, status_of
from app.blockchain.values import quantity, units

ADDRESS = "0x" + "12" * 20
TX = "0x" + "ab" * 32


@pytest.mark.parametrize("value", [True, -1, 1.2, "1.2", "-1", "0xzz", 2**256, None])
def test_invalid_quantities_do_not_become_zero(value):
    assert quantity(value) is None


def test_exact_amount_conversion_and_missing_value():
    number = 123456789012345678901234567890
    assert units(number, 18) == "123456789012.34567890123456789"
    assert units(1, 0) == "1"
    assert units(0, 18) == "0"
    assert units(1, 30) == "0." + "0" * 29 + "1"
    detail, _, _, _ = merge_transaction([("e", "explorer", {"value": "0"})], "COIN", 18)
    assert detail.value_display == "0"
    detail, _, _, _ = merge_transaction([], "COIN", 18)
    assert detail.value_display is None


def test_conflicting_sources_do_not_produce_confirmed_fields():
    detail, status, ids, issues = merge_transaction(
        [
            ("e", "explorer", {"status": "ok", "value": "1", "block_number": 5}),
            ("r", "receipt", {"status": "0x0", "blockNumber": "0x6", "value": "2"}),
        ],
        "COIN",
        18,
    )
    assert status == "unknown" and detail.value_raw is None and detail.block_number is None
    assert set(ids) == {"e", "r"} and issues


def test_no_selector_identity_is_invented():
    detail, _, _, _ = merge_transaction(
        [("e", "explorer", {"raw_input": "0xa9059cbb"})], "COIN", 18
    )
    assert detail.selector == "0xa9059cbb"
    assert detail.field_sources["selector"] == ["e"]
    assert "function" not in detail.model_dump()


def test_pending_requires_explicit_evidence():
    assert status_of({}, "transaction") == "unknown"
    assert status_of({"blockNumber": None}, "transaction") == "pending"
    assert status_of({"blockNumber": "0x1"}, "transaction") == "unknown"
    assert status_of({"status": "0x1"}, "receipt") == "unknown"


def test_logs_preserve_raw_data_reject_removed_and_deduplicate_by_index():
    log = {
        "address": {"hash": ADDRESS},
        "data": "0x00",
        "topics": ["0x" + "11" * 32, None],
        "index": 0,
    }
    logs, rejected = normalize_logs(
        [log, log, {**log, "removed": True}, {**log, "topics": ["invalid"]}], "logs", TX
    )
    assert len(logs) == 1 and rejected == 2
    assert logs[0].topics == ["0x" + "11" * 32]
    assert logs[0].data == "0x00"
