"""Deterministic guards around untrusted external text and model output.

External content (source comments, revert strings, token metadata, decoded string arguments)
is evidence, never instructions. These checks do not decide anything for the model; they make
suspicious content visible and keep model text tied to identifiers present in the evidence.
"""

import re
from collections.abc import Iterator

from pydantic import BaseModel

from app.domain.analysis import AnalysisResponse, Finding

INSTRUCTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|"
        r"system|your)\b[^.\n]{0,20}\b(instructions?|prompts?|rules?|messages?)",
        r"\b(ignore|desconsidere|esqueça|esqueca)\b[^.\n]{0,40}\b(instruç(ão|ões)|instrucoes|"
        r"instrucao|regras|prompt)",
        r"\bsystem\s*prompt\b",
        r"\byou\s+are\s+now\b",
        r"\bvocê\s+agora\s+é\b",
        r"\b(new|updated)\s+instructions?\s*:",
        r"\b(assistant|ai|llm|model|language model)\s*[,:]\s*(you must|please|always|never)\b",
        r"</?\s*(evidence|system|instructions?)\s*>",
        r"\bsubmit_analysis\b",
    )
]
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
HASH = re.compile(r"0x[0-9a-fA-F]{64}(?![0-9a-fA-F])")
DELIMITER = re.compile(r"<(?=\s*/?\s*evidence)", re.IGNORECASE)
MAX_FLAGGED = 5


def instruction_like(text: str) -> bool:
    return any(pattern.search(text) for pattern in INSTRUCTION_PATTERNS)


def escape_markup(text: str) -> str:
    """JSON-safe escaping so untrusted strings cannot open or close the evidence delimiter.

    Only "<" starting an evidence tag is escaped, so code such as `a < b` stays readable.
    """
    return DELIMITER.sub("\\\\u003c", text)


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _external_texts(response: AnalysisResponse) -> Iterator[tuple[str, str, list[str]]]:
    """(label, text, evidence ids) for every piece of externally authored free text."""
    for excerpt in response.contract_context:
        label = f"código-fonte de {excerpt.symbol} ({excerpt.path})"
        yield label, excerpt.code, excerpt.evidence_ids
    revert = response.diagnosis.revert
    if revert and revert.message:
        yield "mensagem de revert", revert.message, revert.evidence_ids
    if revert:
        for arg in revert.arguments:
            for text in _strings(arg.value):
                yield "argumento do erro", text, revert.evidence_ids
    call = response.transaction.decoded_input
    if call:
        for arg in call.arguments:
            for text in _strings(arg.value):
                yield f"argumento {arg.name or arg.type} da chamada", text, call.evidence_ids
        for inner in call.nested_calls:
            for arg in inner.arguments:
                for text in _strings(arg.value):
                    label = f"argumento {arg.name or arg.type} de {inner.function}"
                    yield label, text, call.evidence_ids
    for event in response.events:
        if event.decoded:
            for arg in event.decoded.arguments:
                for text in _strings(arg.value):
                    label = f"argumento do evento {event.decoded.name}"
                    yield label, text, event.decoded.evidence_ids
    for transfer in response.transfers:
        if transfer.token_symbol:
            yield "símbolo de token", transfer.token_symbol, transfer.evidence_ids


def flag_untrusted_text(response: AnalysisResponse) -> int:
    """Add a deterministic note for external text that looks like instructions to an AI."""
    flagged: dict[str, list[str]] = {}
    for label, text, ids in _external_texts(response):
        if instruction_like(text) and label not in flagged:
            flagged[label] = ids
    for label, ids in list(flagged.items())[:MAX_FLAGGED]:
        response.security_notes.append(
            Finding(
                description=(
                    f"Conteúdo externo ({label}) contém texto que parece uma instrução para "
                    "sistemas de IA. Foi tratado apenas como dado e não alterou a análise; "
                    "este padrão merece revisão."
                ),
                evidence_ids=ids,
            )
        )
    return len(flagged)


def unknown_identifiers(text: str, known: str) -> list[str]:
    """Addresses and 32-byte hashes in model text that appear nowhere in the evidence."""
    found = [*HASH.findall(text), *ADDRESS.findall(text)]
    return list(dict.fromkeys(item.lower() for item in found if item.lower() not in known))


def known_text(response: AnalysisResponse, extra: list[str]) -> str:
    return (response.model_dump_json() + "\n".join(extra)).lower()


def prune_dangling_citations(response: AnalysisResponse) -> int:
    """Remove evidence ids that do not resolve to a returned source. Returns how many."""
    valid = {source.id for source in response.sources}
    removed = 0

    def keep(ids: list[str]) -> list[str]:
        nonlocal removed
        kept = [i for i in ids if i in valid]
        removed += len(ids) - len(kept)
        return kept

    def walk(value: object) -> None:
        if isinstance(value, BaseModel):
            for name in type(value).model_fields:
                item = getattr(value, name)
                if name.endswith("evidence_ids") and isinstance(item, list):
                    setattr(value, name, keep(item))
                elif name == "field_sources" and isinstance(item, dict):
                    setattr(value, name, {k: keep(v) for k, v in item.items()})
                elif name != "sources":
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(response)
    return removed
