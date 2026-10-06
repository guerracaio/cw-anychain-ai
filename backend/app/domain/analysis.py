from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.domain.transaction import (
    DecodedArg,
    IndexedTransfer,
    InternalCall,
    RawLog,
    TransactionDetails,
)

AnalysisMode = Literal["developer", "support", "auditor"]


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tx_hash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$", min_length=66, max_length=66)
    mode: AnalysisMode = "developer"


class Evidence(BaseModel):
    id: str
    source_type: Literal["blockscout", "rpc", "repository", "documentation", "decoder"]
    source_url: str | None = None
    description: str
    payload: JsonValue
    confidence: Literal["observed", "decoded", "inferred"]


class Finding(BaseModel):
    description: str
    evidence_ids: list[str] = Field(default_factory=list)


class SourceExcerpt(BaseModel):
    """A bounded slice of contract source. Untrusted text: evidence, never instructions."""

    origin: Literal["explorer", "repository"]
    contract: str
    address: str | None = None
    symbol: str
    kind: str
    # Why this excerpt was selected: contract_header, called_function, related_modifier, ...
    reason: str
    path: str
    start_line: int
    end_line: int
    code: str
    truncated: bool = False
    repository: str | None = None
    commit: str | None = None
    url: str | None = None
    evidence_ids: list[str]


class ExplanationFinding(BaseModel):
    kind: Literal["observed", "decoded", "state", "source", "inference"]
    statement: str
    evidence_ids: list[str]


class LlmUsage(BaseModel):
    provider: str
    model: str
    steps: int
    tool_calls: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    estimated_cost_usd: float | None = None
    duration_ms: int = 0


class Explanation(BaseModel):
    """Model-written interpretation. Every item cites evidence ids validated by the harness."""

    summary: str
    findings: list[ExplanationFinding] = Field(default_factory=list)
    likely_causes: list[Finding] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    security_notes: list[Finding] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    # Items removed because they cited no valid evidence (never shown as facts).
    discarded_items: int = 0
    # Addresses/hashes in the free text that no evidence contains (possible hallucination).
    unverified_identifiers: list[str] = Field(default_factory=list)
    usage: LlmUsage


class RevertInfo(BaseModel):
    """Why execution stopped, as reported by a replay or the explorer. Never inferred."""

    source: Literal["rpc_replay", "explorer"]
    kind: Literal["error_string", "panic", "custom_error", "empty", "unknown", "out_of_gas"]
    signature: str | None = None
    name: str | None = None
    # Error(string) text or Panic meaning. Untrusted contract text.
    message: str | None = None
    arguments: list[DecodedArg] = Field(default_factory=list)
    raw: str | None = None
    # Contract whose ABI declared a custom error.
    abi_address: str | None = None
    ambiguous: bool = False
    evidence_ids: list[str]


class StateRead(BaseModel):
    contract: str
    signature: str
    arguments: list[str]
    block: str
    outputs: list[DecodedArg]
    evidence_ids: list[str]


class Diagnosis(BaseModel):
    confirmed: list[Finding] = Field(default_factory=list)
    likely_causes: list[Finding] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    revert: RevertInfo | None = None
    # Outcome of re-executing the call on the pre-block state.
    replay: Literal["reverted", "succeeded", "unavailable", "not_attempted"] | None = None
    state_reads: list[StateRead] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    """Deterministic evidence, ABI decoding and source excerpts; LLM reasoning arrives later."""

    network: str
    tx_hash: str
    status: Literal["success", "failed", "pending", "unknown"]
    summary: str
    request_id: str
    explorer_url: str
    transaction: TransactionDetails
    status_evidence_ids: list[str] = Field(default_factory=list)
    calls: list[InternalCall] = Field(default_factory=list)
    transfers: list[IndexedTransfer] = Field(default_factory=list)
    events: list[RawLog] = Field(default_factory=list)
    diagnosis: Diagnosis = Field(default_factory=Diagnosis)
    contract_context: list[SourceExcerpt] = Field(default_factory=list)
    security_notes: list[Finding] = Field(default_factory=list)
    sources: list[Evidence] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    mode: AnalysisMode = "developer"
    explanation: Explanation | None = None
    # Why there is no explanation (e.g. llm_not_configured); null when one was produced.
    explanation_issue: str | None = None


class ApiError(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ApiError
