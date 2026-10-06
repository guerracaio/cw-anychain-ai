"""Lightweight Solidity symbol extraction. Text analysis only: source is never compiled or run.

Source content is untrusted evidence. This module only locates line ranges.
"""

import re
from dataclasses import dataclass, field

CONTRACT = re.compile(r"\b(abstract\s+contract|contract|interface|library)\s+([A-Za-z_$][\w$]*)")
MEMBER = re.compile(r"\b(function|modifier|event|error)\s+([A-Za-z_$][\w$]*)\s*\(")
IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
REVERTED_ERROR = re.compile(r"\brevert\s+([A-Za-z_$][\w$]*)\s*\(")
CALL = re.compile(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(")
REQUIRED_ERROR = re.compile(r"\brequire\s*\([^;]*?,\s*([A-Za-z_$][\w$]*)\s*\(")
NOT_MODIFIERS = {
    "public",
    "external",
    "internal",
    "private",
    "view",
    "pure",
    "payable",
    "virtual",
    "override",
    "returns",
    "memory",
    "calldata",
    "storage",
}


@dataclass
class Declaration:
    kind: str
    name: str
    start: int
    end: int
    # Offset where the declaration body starts ("{"), or None for declarations without body.
    body_start: int | None = None
    container: str | None = None
    bases: list[str] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Names called from the body; callers keep only those resolving to internal functions.
    calls: list[str] = field(default_factory=list)
    internal: bool = False


def mask(source: str, strings: bool = True) -> str:
    """Blank comments (and string literals unless strings=False), keeping offsets intact."""
    out = list(source)
    i = 0
    n = len(source)

    def blank(start: int, end: int) -> None:
        for k in range(start, min(end, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        two = source[i : i + 2]
        if two == "//":
            end = source.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
        elif two == "/*":
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            blank(i, end)
            i = end
        elif source[i] in "\"'":
            quote = source[i]
            k = i + 1
            while k < n and source[k] != quote and source[k] != "\n":
                k += 2 if source[k] == "\\" else 1
            if strings:
                blank(i + 1, k)
            i = k + 1
        else:
            i += 1
    return "".join(out)


def _match(masked: str, start: int, opening: str, closing: str) -> int:
    """Index after the bracket closing the one at `start`, or len(masked) if unbalanced."""
    depth = 0
    for k in range(start, len(masked)):
        if masked[k] == opening:
            depth += 1
        elif masked[k] == closing:
            depth -= 1
            if depth == 0:
                return k + 1
    return len(masked)


def _top_level_identifiers(text: str) -> list[str]:
    names = []
    depth = 0
    for token in re.finditer(r"[()]|[A-Za-z_$][\w$]*", text):
        value = token.group()
        if value == "(":
            depth += 1
        elif value == ")":
            depth -= 1
        elif depth == 0:
            names.append(value)
    return names


def parse(source: str) -> list[Declaration]:
    masked = mask(source)
    declarations: list[Declaration] = []
    for found in CONTRACT.finditer(masked):
        body = masked.find("{", found.end())
        if body < 0:
            continue
        header = masked[found.end() : body]
        bases = []
        if re.match(r"\s*is\b", header):
            bases = _top_level_identifiers(header.split("is", 1)[1])
        end = _match(masked, body, "{", "}")
        declarations.append(
            Declaration("contract", found.group(2), found.start(), end, body, None, bases)
        )
    for found in MEMBER.finditer(masked):
        kind, name = found.group(1), found.group(2)
        params_end = _match(masked, found.end() - 1, "(", ")")
        k = params_end
        while k < len(masked) and masked[k] not in "{;":
            k += 1
        body_start = k if k < len(masked) and masked[k] == "{" else None
        end = _match(masked, k, "{", "}") if body_start is not None else k + 1
        container = next(
            (
                c.name
                for c in declarations
                if c.kind == "contract"
                and c.body_start is not None
                and c.body_start < found.start() < c.end
            ),
            None,
        )
        declaration = Declaration(kind, name, found.start(), end, body_start, container)
        if kind in ("function", "modifier"):
            # Arguments of returns(...) and override(...) are skipped as parenthesized groups.
            attributes = _top_level_identifiers(masked[params_end : body_start or end])
            declaration.internal = "internal" in attributes or "private" in attributes
            declaration.modifiers = list(
                dict.fromkeys(item for item in attributes if item not in NOT_MODIFIERS)
            )
        if body_start is not None:
            body = masked[body_start:end]
            declaration.errors = list(
                dict.fromkeys([*REVERTED_ERROR.findall(body), *REQUIRED_ERROR.findall(body)])
            )
            declaration.calls = list(dict.fromkeys(CALL.findall(body)))
        declarations.append(declaration)
    return declarations


def line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def excerpt_range(source: str, declaration: Declaration) -> tuple[int, int]:
    """1-based inclusive line range, extended upward over the NatSpec/comment block."""
    lines = source.splitlines()
    start = line_of(source, declaration.start)
    end = line_of(source, max(declaration.end - 1, declaration.start))
    while start > 1:
        previous = lines[start - 2].strip()
        if previous.startswith(("///", "/*", "*", "//")) or previous.endswith("*/"):
            start -= 1
        else:
            break
    if declaration.kind == "contract":
        # A contract excerpt shows its header and the opening of the body only.
        body_line = line_of(source, declaration.body_start or declaration.start)
        end = min(end, body_line)
    return start, end
