"""Offline model evaluation: fixed cases, frozen evidence, deterministic scoring.

Each case freezes the deterministic analysis (snapshot), so every model explains exactly the
same evidence. Scoring is rule-based (no LLM judge): expected mentions, required citations,
grounding (discarded items and unverified identifiers), tool budget and forbidden claims.
"""

import unicodedata
from pathlib import Path
from statistics import mean
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.analysis import AnalysisMode, AnalysisResponse, Explanation


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Expectations(Strict):
    # Each group is satisfied when any of its terms appears (accent- and case-insensitive).
    mention: list[list[str]] = Field(default_factory=list)
    # Each group needs at least one cited evidence id starting with one of its prefixes.
    cite: list[list[str]] = Field(default_factory=list)
    likely_cause: bool = False
    max_tool_calls: int | None = None
    forbid: list[str] = Field(default_factory=list)


class Collection(Strict):
    """How the snapshot is produced: a live deterministic run or an existing example file."""

    source: Literal["live", "example"] = "live"
    example: str | None = None
    # Live runs can drop the RPC to reproduce a partial external failure.
    rpc: bool = True


class Case(Strict):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    category: str
    tx_hash: str = Field(pattern=r"^0x[0-9a-f]{64}$")
    mode: AnalysisMode = "developer"
    collect: Collection = Field(default_factory=Collection)
    expect: Expectations = Field(default_factory=Expectations)


class ModelSpec(Strict):
    provider: str
    model: str
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    thinking_level: Literal["minimal", "low", "medium", "high"] | None = None

    @field_validator("provider")
    @classmethod
    def lower(cls, value: str) -> str:
        return value.lower()

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


class Check(BaseModel):
    name: str
    passed: bool


class RunResult(BaseModel):
    case: str
    model: str
    explained: bool
    issue: str | None = None
    checks: list[Check] = Field(default_factory=list)
    score: float = 0.0
    discarded_items: int = 0
    unverified_identifiers: int = 0
    cited_ids: int = 0
    steps: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cost_usd: float | None = None
    duration_ms: int = 0
    summary: str | None = None


class ModelSummary(BaseModel):
    model: str
    runs: int
    explained: int
    mean_score: float
    fully_passed: int
    discarded_items: int
    unverified_identifiers: int
    mean_steps: float
    mean_tool_calls: float
    mean_tokens: float
    mean_duration_ms: float
    total_cost_usd: float | None
    issues: dict[str, int]


def load_cases(path: Path) -> list[Case]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = [Case.model_validate(item) for item in data["cases"]]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case ids")
    return cases


def load_models(path: Path) -> list[ModelSpec]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [ModelSpec.model_validate(item) for item in data["models"]]


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def explanation_text(explanation: Explanation) -> str:
    parts = [
        explanation.summary,
        *(f.statement for f in explanation.findings),
        *(c.description for c in explanation.likely_causes),
        *explanation.next_steps,
        *(n.description for n in explanation.security_notes),
        *explanation.uncertainties,
    ]
    return normalize("\n".join(parts))


def cited_ids(explanation: Explanation) -> set[str]:
    items = [*explanation.findings, *explanation.likely_causes, *explanation.security_notes]
    return {i for item in items for i in item.evidence_ids}


def score(case: Case, response: AnalysisResponse, model: str) -> RunResult:
    explanation = response.explanation
    if explanation is None:
        return RunResult(
            case=case.id, model=model, explained=False, issue=response.explanation_issue
        )
    text = explanation_text(explanation)
    ids = cited_ids(explanation)
    usage = explanation.usage
    expect = case.expect
    checks = [
        Check(name=f"mention:{group[0]}", passed=any(normalize(t) in text for t in group))
        for group in expect.mention
    ]
    checks += [
        Check(
            name=f"cite:{group[0]}",
            passed=any(i.startswith(prefix) for i in ids for prefix in group),
        )
        for group in expect.cite
    ]
    checks.append(
        Check(
            name="grounded",
            passed=explanation.discarded_items == 0 and not explanation.unverified_identifiers,
        )
    )
    if expect.likely_cause:
        checks.append(Check(name="likely_cause", passed=bool(explanation.likely_causes)))
    if expect.max_tool_calls is not None:
        checks.append(
            Check(name="tool_budget", passed=len(usage.tool_calls) <= expect.max_tool_calls)
        )
    if expect.forbid:
        checks.append(
            Check(name="forbid", passed=not any(normalize(t) in text for t in expect.forbid))
        )
    return RunResult(
        case=case.id,
        model=model,
        explained=True,
        checks=checks,
        score=round(sum(c.passed for c in checks) / len(checks), 4),
        discarded_items=explanation.discarded_items,
        unverified_identifiers=len(explanation.unverified_identifiers),
        cited_ids=len(ids),
        steps=usage.steps,
        tool_calls=len(usage.tool_calls),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        thinking_tokens=usage.thinking_tokens,
        cost_usd=usage.estimated_cost_usd,
        duration_ms=usage.duration_ms,
        summary=explanation.summary,
    )


def summarize(results: list[RunResult]) -> list[ModelSummary]:
    summaries = []
    for model in dict.fromkeys(r.model for r in results):
        runs = [r for r in results if r.model == model]
        explained = [r for r in runs if r.explained]
        issues: dict[str, int] = {}
        for run in runs:
            if run.issue:
                issues[run.issue] = issues.get(run.issue, 0) + 1
        costs = [r.cost_usd for r in explained]

        def avg(values: list[float]) -> float:
            return round(mean(values), 2) if values else 0.0

        summaries.append(
            ModelSummary(
                model=model,
                runs=len(runs),
                explained=len(explained),
                # Failed runs count as zero: reliability is part of quality.
                mean_score=round(mean(r.score for r in runs), 4),
                fully_passed=sum(1 for r in explained if r.score == 1.0),
                discarded_items=sum(r.discarded_items for r in explained),
                unverified_identifiers=sum(r.unverified_identifiers for r in explained),
                mean_steps=avg([r.steps for r in explained]),
                mean_tool_calls=avg([r.tool_calls for r in explained]),
                mean_tokens=avg(
                    [r.input_tokens + r.output_tokens + r.thinking_tokens for r in explained]
                ),
                mean_duration_ms=avg([r.duration_ms for r in explained]),
                total_cost_usd=(
                    round(sum(c for c in costs if c is not None), 6)
                    if costs and all(c is not None for c in costs)
                    else None
                ),
                issues=issues,
            )
        )
    return sorted(summaries, key=lambda s: (-s.mean_score, s.total_cost_usd or 0.0))


def render_markdown(results: list[RunResult], summaries: list[ModelSummary]) -> str:
    lines = [
        "| Modelo | Explicadas | Pontuação média | 100% | Descartes | Ids não verificados "
        "| Passos | Ferramentas | Tokens | Latência (ms) | Custo total (US$) | Falhas |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in summaries:
        cost = f"{s.total_cost_usd:.4f}" if s.total_cost_usd is not None else "-"
        issues = ", ".join(f"{k}×{v}" for k, v in s.issues.items()) or "-"
        lines.append(
            f"| {s.model} | {s.explained}/{s.runs} | {s.mean_score:.2f} | {s.fully_passed} "
            f"| {s.discarded_items} | {s.unverified_identifiers} | {s.mean_steps} "
            f"| {s.mean_tool_calls} | {s.mean_tokens:.0f} | {s.mean_duration_ms:.0f} "
            f"| {cost} | {issues} |"
        )
    lines += [
        "",
        "| Caso | Modelo | Pontuação | Verificações que falharam |",
        "| --- | --- | --- | --- |",
    ]
    for r in results:
        failed = (
            ", ".join(c.name for c in r.checks if not c.passed) or "-"
            if r.explained
            else f"sem explicação ({r.issue})"
        )
        lines.append(f"| {r.case} | {r.model} | {r.score:.2f} | {failed} |")
    return "\n".join(lines) + "\n"
