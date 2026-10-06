from app.repositories.solidity import excerpt_range, mask, parse

SOURCE = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

error Unauthorized(address caller);

/// @notice Token with a pause switch. "}" in comments must not break parsing.
contract Token is Base, Pausable(1), IERC20 {
    string constant NOTE = "function fake() { }";

    event Moved(address indexed to, uint256 amount);
    error TooLarge(uint256 amount);

    /**
     * @dev Moves tokens.
     */
    function transfer(address to, uint256 amount)
        external
        override(IERC20, Base)
        whenNotPaused
        onlyRole(MINTER)
        whenNotPaused
        returns (bool)
    {
        if (amount > 10) {
            revert TooLarge(amount);
        }
        require(to != address(0), Unauthorized(msg.sender));
        _move(to, amount);
        return true;
    }

    function _move(address to, uint256 amount) internal virtual {
        emit Moved(to, amount);
    }
}

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
}
"""


def declarations(kind, name):
    return [d for d in parse(SOURCE) if d.kind == kind and d.name == name]


def test_mask_preserves_offsets_and_hides_comments_and_strings():
    masked = mask(SOURCE)
    assert len(masked) == len(SOURCE)
    assert masked.count("\n") == SOURCE.count("\n")
    assert "function fake" not in masked and "in comments" not in masked


def test_contract_bases_and_body():
    (token,) = declarations("contract", "Token")
    assert token.bases == ["Base", "Pausable", "IERC20"]
    assert SOURCE[token.end - 1] == "}"
    assert [d.name for d in parse(SOURCE) if d.kind == "function" and d.container == "Token"] == [
        "transfer",
        "_move",
    ]


def test_function_attributes_errors_and_calls():
    implemented, declared = declarations("function", "transfer")
    assert implemented.container == "Token" and implemented.body_start is not None
    assert implemented.modifiers == ["whenNotPaused", "onlyRole"]
    assert implemented.errors == ["TooLarge", "Unauthorized"]
    assert "_move" in implemented.calls
    assert not implemented.internal
    assert declared.container == "IERC20" and declared.body_start is None
    (move,) = declarations("function", "_move")
    assert move.internal


def test_excerpt_includes_natspec_and_full_body():
    (transfer, _) = declarations("function", "transfer")
    start, end = excerpt_range(SOURCE, transfer)
    lines = SOURCE.splitlines()
    assert lines[start - 1].strip() == "/**"
    assert lines[end - 1].strip() == "}" and "return true" in lines[end - 2]


def test_contract_excerpt_is_header_only():
    (token,) = declarations("contract", "Token")
    start, end = excerpt_range(SOURCE, token)
    assert SOURCE.splitlines()[start - 1].startswith("/// @notice")
    assert SOURCE.splitlines()[end - 1].startswith("contract Token is")


def test_file_level_error_has_no_container():
    (error,) = declarations("error", "Unauthorized")
    assert error.container is None
