"""Explicit, bounded tool-calling loop: the model reasons, the harness executes and validates."""

import asyncio
import logging
import time

from pydantic import ValidationError

from app.agent.prompts import SYSTEM, user_message
from app.agent.schemas import SUBMIT_PARAMETERS, SUBMIT_TOOL, CitedItem, SubmittedAnalysis
from app.agent.tools import AgentTools
from app.config.models import AppConfig
from app.domain.analysis import (
    AnalysisResponse,
    Explanation,
    ExplanationFinding,
    Finding,
    LlmUsage,
)
from app.llm.base import LLMError, LLMProvider, ToolResult, ToolSpec, Usage
from app.services.http import request_id
from app.services.safety import known_text, unknown_identifiers

logger = logging.getLogger("anychain.agent")

SUBMIT_SPEC = ToolSpec(
    SUBMIT_TOOL,
    "Submit the final grounded analysis. Call exactly once, when done.",
    SUBMIT_PARAMETERS,
)
TEXT_NOTE = f"Respond only with tool calls. Call {SUBMIT_TOOL} when you are done."
LAST_STEP_NOTE = f"This is the last step: call {SUBMIT_TOOL} now with what you have."


def needs_tools(response: AnalysisResponse) -> bool:
    """Contract interactions may need more evidence; plain native transfers do not."""
    tx = response.transaction
    return bool(
        tx.selector
        or tx.created_contract
        or response.events
        or response.transfers
        or response.calls
        or tx.recipient_is_contract
    )


def estimate_cost(config: AppConfig, usage: Usage) -> float | None:
    llm = config.llm
    if llm.input_price_per_million is None or llm.output_price_per_million is None:
        return None
    billed_output = usage.output_tokens + usage.thinking_tokens
    cost = usage.input_tokens * llm.input_price_per_million
    return round((cost + billed_output * llm.output_price_per_million) / 1_000_000, 6)


def ground(
    submitted: SubmittedAnalysis, valid_ids: set[str], include_security_notes: bool, known: str
) -> tuple[dict, int]:
    """Keep only cited ids that exist; items left without evidence are discarded.

    Cited items that mention an address or hash absent from all evidence are discarded too;
    free text (summary, steps, uncertainties) is kept but its unknown identifiers are listed.
    """
    discarded = 0

    def cited(ids: list[str], text: str) -> list[str]:
        if unknown_identifiers(text, known):
            return []
        return [i for i in dict.fromkeys(ids) if i in valid_ids]

    findings = []
    for item in submitted.findings:
        ids = cited(item.evidence_ids, item.statement)
        if ids:
            findings.append(
                ExplanationFinding(kind=item.kind, statement=item.statement, evidence_ids=ids)
            )
        else:
            discarded += 1

    def findings_of(items: list[CitedItem]) -> list[Finding]:
        nonlocal discarded
        kept = []
        for item in items:
            ids = cited(item.evidence_ids, item.description)
            if ids:
                kept.append(Finding(description=item.description, evidence_ids=ids))
            else:
                discarded += 1
        return kept

    fields = {
        "summary": submitted.summary,
        "findings": findings,
        "likely_causes": findings_of(submitted.likely_causes),
        "next_steps": submitted.next_steps,
        "security_notes": (findings_of(submitted.security_notes) if include_security_notes else []),
        "uncertainties": submitted.uncertainties,
    }
    fields["discarded_items"] = discarded
    free_text = "\n".join([submitted.summary, *submitted.next_steps, *submitted.uncertainties])
    fields["unverified_identifiers"] = unknown_identifiers(free_text, known)
    return fields, discarded


async def run_agent(
    provider: LLMProvider,
    tools: AgentTools,
    response: AnalysisResponse,
    config: AppConfig,
    mode: str,
) -> Explanation:
    limits = config.analysis
    started = time.monotonic()
    session = provider.start(
        system=SYSTEM,
        user=user_message(response, mode, limits.max_llm_context_chars),
        # A plain value transfer has nothing for tools to inspect: answer in one step.
        tools=[*tools.specs(), SUBMIT_SPEC] if needs_tools(response) else [SUBMIT_SPEC],
        max_output_tokens=limits.max_output_tokens,
    )
    usage = Usage()
    called: list[str] = []
    results: list[ToolResult] = []
    note: str | None = None
    for step in range(1, limits.max_agent_steps + 1):
        if step == limits.max_agent_steps and step > 1:
            note = "\n".join(filter(None, [note, LAST_STEP_NOTE]))
        step_started = time.monotonic()
        turn = await session.step(results, note)
        usage.add(turn.usage)
        results, note = [], None
        names = [call.name for call in turn.tool_calls]
        logger.info(
            "request_id=%s provider=%s model=%s step=%d tools=%s input_tokens=%d "
            "output_tokens=%d thinking_tokens=%d duration_ms=%d",
            request_id.get(),
            provider.name,
            provider.model,
            step,
            ",".join(names) or "-",
            turn.usage.input_tokens,
            turn.usage.output_tokens,
            turn.usage.thinking_tokens,
            (time.monotonic() - step_started) * 1000,
        )
        if not turn.tool_calls:
            note = TEXT_NOTE
            continue
        submit = next((c for c in turn.tool_calls if c.name == SUBMIT_TOOL), None)
        if submit is not None:
            try:
                submitted = SubmittedAnalysis.model_validate(submit.arguments)
            except ValidationError as exc:
                fields = sorted({".".join(map(str, e["loc"])) for e in exc.errors()})
                results = [
                    ToolResult(c, {"error": "invalid_submission", "fields": fields})
                    if c is submit
                    else ToolResult(c, {"error": "not_executed_with_submit"})
                    for c in turn.tool_calls
                ]
                continue
            valid_ids = {source.id for source in response.sources}
            fields, discarded = ground(
                submitted,
                valid_ids,
                limits.include_security_notes,
                known_text(response, tools.returned),
            )
            explanation = Explanation(
                **fields,
                usage=LlmUsage(
                    provider=provider.name,
                    model=provider.model,
                    steps=step,
                    tool_calls=called,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    thinking_tokens=usage.thinking_tokens,
                    estimated_cost_usd=estimate_cost(config, usage),
                    duration_ms=int((time.monotonic() - started) * 1000),
                ),
            )
            logger.info(
                "request_id=%s provider=%s model=%s agent_steps=%d tool_calls=%d "
                "input_tokens=%d output_tokens=%d thinking_tokens=%d discarded_items=%d "
                "unverified_identifiers=%d estimated_cost_usd=%s duration_ms=%d",
                request_id.get(),
                provider.name,
                provider.model,
                step,
                len(called),
                usage.input_tokens,
                usage.output_tokens,
                usage.thinking_tokens,
                discarded,
                len(explanation.unverified_identifiers),
                explanation.usage.estimated_cost_usd,
                explanation.usage.duration_ms,
            )
            return explanation
        called.extend(names)
        outputs = await asyncio.gather(*(tools.execute(call) for call in turn.tool_calls))
        results = [
            ToolResult(call, output) for call, output in zip(turn.tool_calls, outputs, strict=True)
        ]
    raise LLMError("agent_step_limit")
