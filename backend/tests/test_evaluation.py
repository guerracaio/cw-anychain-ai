import asyncio
from pathlib import Path

import httpx
import pytest
from llm_fakes import FakeProvider, submit
from test_analysis import TX, config, token_handler

from app.config.loader import PROJECT_ROOT
from app.domain.analysis import AnalysisResponse
from app.evaluation import (
    Case,
    ModelSpec,
    load_cases,
    load_models,
    render_markdown,
    score,
    summarize,
)
from app.main import build_services

CASE = Case.model_validate(
    {
        "id": "erc20",
        "category": "ERC-20",
        "tx_hash": TX,
        "expect": {
            "mention": [["transferência", "transfer"], ["49,19", "49.19"]],
            "cite": [["decoder.input"], ["explorer.source", "explorer.abi"]],
            "likely_cause": True,
            "max_tool_calls": 0,
            "forbid": ["falhou"],
        },
    }
)


def explained(**fields) -> AnalysisResponse:
    """Run the real agent harness over a mocked analysis with a scripted provider."""

    async def run():
        transport = httpx.MockTransport(token_handler())
        async with build_services(config(rpc=False), transport=transport, use_llm=False) as base:
            response = await base.analyze(TX, "eval")
        async with build_services(
            config(rpc=False), transport=transport, llm=FakeProvider([submit(**fields)])
        ) as service:
            await service.explain(response, "developer")
        return response

    return asyncio.run(run())


def test_score_counts_each_expectation():
    response = explained(
        summary="Transferência de 49,19 tokens.",
        findings=[
            {"kind": "decoded", "statement": "Chamou transfer.", "evidence_ids": ["decoder.input"]}
        ],
    )
    result = score(CASE, response, "fake:model")
    checks = {c.name: c.passed for c in result.checks}
    assert checks == {
        "mention:transferência": True,
        "mention:49,19": True,
        "cite:decoder.input": True,
        "cite:explorer.source": False,
        "grounded": True,
        "likely_cause": False,
        "tool_budget": True,
        "forbid": True,
    }
    assert result.score == 0.75 and result.explained and result.cost_usd is None


def test_mentions_ignore_case_and_accents_and_forbidden_terms_fail():
    response = explained(summary="TRANSFERENCIA de 49.19 que FALHOU.")
    checks = {c.name: c.passed for c in score(CASE, response, "m").checks}
    assert checks["mention:transferência"] and checks["mention:49,19"]
    assert not checks["forbid"]


def test_ungrounded_output_fails_the_grounding_check():
    response = explained(
        summary="Enviado para 0x" + "9f" * 20 + ".",
        findings=[{"kind": "inference", "statement": "Sem fonte.", "evidence_ids": ["nope"]}],
    )
    result = score(CASE, response, "m")
    assert not next(c for c in result.checks if c.name == "grounded").passed
    assert (result.discarded_items, result.unverified_identifiers) == (1, 1)


def test_missing_explanation_scores_zero_and_is_summarized():
    response = explained()
    response.explanation, response.explanation_issue = None, "llm_rate_limited"
    failed = score(CASE, response, "a:slow")
    good = score(CASE, explained(summary="transfer de 49,19"), "b:fast")
    summaries = summarize([failed, good])
    assert [s.model for s in summaries] == ["b:fast", "a:slow"]
    slow = summaries[1]
    assert (slow.explained, slow.mean_score, slow.issues) == (0, 0.0, {"llm_rate_limited": 1})
    report = render_markdown([failed, good], summaries)
    assert "sem explicação (llm_rate_limited)" in report and "| b:fast | 1/1 |" in report


def test_case_and_model_files_are_valid_and_snapshots_match():
    cases = load_cases(PROJECT_ROOT / "evals" / "cases.yaml")
    assert len(cases) >= 8
    for case in cases:
        path = PROJECT_ROOT / "evals" / "snapshots" / f"{case.id}.json"
        snapshot = AnalysisResponse.model_validate_json(path.read_text(encoding="utf-8"))
        assert snapshot.tx_hash == case.tx_hash and snapshot.explanation is None
        # Snapshots must cite only evidence they contain.
        valid = {s.id for s in snapshot.sources}
        assert set(snapshot.status_evidence_ids) <= valid
    models = load_models(PROJECT_ROOT / "evals" / "models.yaml")
    assert models and all(isinstance(m, ModelSpec) for m in models)


def test_invalid_case_definitions_are_rejected(tmp_path: Path):
    path = tmp_path / "cases.yaml"
    path.write_text(
        "cases:\n"
        f"  - {{id: a, category: x, tx_hash: '{TX}'}}\n"
        f"  - {{id: a, category: y, tx_hash: '{TX}'}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_cases(path)
    with pytest.raises(ValueError):
        Case.model_validate({"id": "b", "category": "x", "tx_hash": TX, "unknown": 1})
