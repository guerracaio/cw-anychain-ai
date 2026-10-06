"""Model evaluation over a fixed transaction set.

  python backend/scripts/evaluate.py collect [--refresh] [--cases id ...]
  python backend/scripts/evaluate.py run [--models gemini:gemini-3.8-flash ...] [--cases id ...]
  python backend/scripts/evaluate.py run --retry-failed evals/results/<folder>

`collect` freezes the deterministic analysis of each case (no LLM) in evals/snapshots/.
`run` lets every configured model explain the same snapshots and writes a scored report to
evals/results/. `--retry-failed` re-runs only the runs of a previous result that failed with a
transient provider error (rate limit, overload, timeout) and writes a merged report.
Agent tools still query the configured explorer, RPC and repositories live.
"""

import argparse
import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from app.config.loader import PROJECT_ROOT, load_config
from app.config.models import AppConfig
from app.domain.analysis import AnalysisResponse
from app.evaluation import (
    Case,
    ModelSpec,
    RunResult,
    load_cases,
    load_models,
    render_markdown,
    score,
    summarize,
)
from app.main import build_services, configure_logging
from app.services.http import request_id

EVALS = PROJECT_ROOT / "evals"
SNAPSHOTS = EVALS / "snapshots"
TRANSIENT = {"llm_rate_limited", "llm_unavailable", "llm_timeout"}
EXCLUDE = {"explanation", "explanation_issue", "request_id"}


def snapshot_path(case: Case) -> Path:
    return SNAPSHOTS / f"{case.id}.json"


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def collect(settings: AppConfig, cases: list[Case], refresh: bool) -> None:
    for case in cases:
        path = snapshot_path(case)
        if path.exists() and not refresh:
            print(f"{case.id}: snapshot existente")
            continue
        if case.collect.source == "example":
            assert case.collect.example, f"{case.id}: example path missing"
            data = json.loads((PROJECT_ROOT / case.collect.example).read_text(encoding="utf-8"))
            response = AnalysisResponse.model_validate(
                {k: v for k, v in data.items() if k not in EXCLUDE} | {"request_id": "snapshot"}
            )
        else:
            case_settings = settings
            if not case.collect.rpc:
                case_settings = settings.model_copy(
                    update={"rpc": settings.rpc.model_copy(update={"url": None})}
                )
            async with build_services(case_settings, use_llm=False) as service:
                response = await service.analyze(case.tx_hash, "snapshot", case.mode)
        if response.tx_hash != case.tx_hash:
            raise ValueError(f"{case.id}: snapshot is for another transaction")
        write_json(path, response.model_dump(mode="json", exclude=EXCLUDE - {"request_id"}))
        print(f"{case.id}: {response.status}, {len(response.sources)} evidências")


def settings_for(settings: AppConfig, spec: ModelSpec) -> AppConfig:
    llm = settings.llm.model_copy(
        update={
            "provider": spec.provider,
            "model": spec.model,
            "input_price_per_million": spec.input_price_per_million,
            "output_price_per_million": spec.output_price_per_million,
            "thinking_level": spec.thinking_level,
        }
    )
    return settings.model_copy(update={"llm": llm})


async def run(
    settings: AppConfig,
    plan: list[tuple[ModelSpec, list[Case]]],
    *,
    delay: float,
    retries: int,
    retry_wait: float,
) -> list[RunResult]:
    snapshots = {}
    for case in {c.id: c for _, cases in plan for c in cases}.values():
        path = snapshot_path(case)
        if not path.exists():
            raise SystemExit(f"{case.id}: snapshot ausente; rode 'collect' antes.")
        snapshots[case.id] = json.loads(path.read_text(encoding="utf-8"))
    results = []
    for spec, cases in plan:
        async with build_services(settings_for(settings, spec)) as service:
            for case in cases:
                for attempt in range(retries + 1):
                    response = AnalysisResponse.model_validate(snapshots[case.id])
                    response.mode = case.mode
                    token = request_id.set(f"eval-{case.id}-{spec.model}")
                    try:
                        await service.explain(response, case.mode)
                    finally:
                        request_id.reset(token)
                    if response.explanation_issue not in TRANSIENT or attempt == retries:
                        break
                    print(f"  {case.id}: {response.explanation_issue}, nova tentativa")
                    await asyncio.sleep(retry_wait)
                result = score(case, response, spec.label)
                results.append(result)
                outcome = f"{result.score:.2f}" if result.explained else result.issue
                print(f"{spec.label} {case.id}: {outcome}")
                await asyncio.sleep(delay)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("command", choices=["collect", "run"])
    parser.add_argument("--cases", nargs="*", help="case ids (default: all)")
    parser.add_argument("--models", nargs="*", help="provider:model labels (default: all)")
    parser.add_argument("--refresh", action="store_true", help="re-collect existing snapshots")
    parser.add_argument("--delay", type=float, default=4.0, help="seconds between runs")
    parser.add_argument("--retries", type=int, default=1, help="retries on transient LLM errors")
    parser.add_argument("--retry-wait", type=float, default=30.0)
    parser.add_argument("--label", default="run")
    parser.add_argument("--retry-failed", type=Path, help="previous result folder to complete")
    args = parser.parse_args()

    configure_logging()
    # Per-request HTTP lines are noise here; the per-analysis and agent lines remain.
    logging.getLogger("anychain.tools").setLevel(logging.WARNING)
    settings = load_config()
    cases = load_cases(EVALS / "cases.yaml")
    if args.cases:
        unknown = set(args.cases) - {c.id for c in cases}
        if unknown:
            raise SystemExit(f"Casos desconhecidos: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c.id in args.cases]
    if args.command == "collect":
        asyncio.run(collect(settings, cases, args.refresh))
        return
    models = load_models(EVALS / "models.yaml")
    if args.models:
        models = [m for m in models if m.label in args.models]
        if not models:
            raise SystemExit("Nenhum modelo de evals/models.yaml corresponde a --models.")
    previous: list[RunResult] = []
    plan = [(spec, cases) for spec in models]
    if args.retry_failed:
        data = json.loads((args.retry_failed / "results.json").read_text(encoding="utf-8"))
        previous = [RunResult.model_validate(r) for r in data["runs"]]
        pending = {(r.model, r.case) for r in previous if not r.explained and r.issue in TRANSIENT}
        plan = [(spec, [c for c in cases if (spec.label, c.id) in pending]) for spec in models]
        plan = [(spec, selected) for spec, selected in plan if selected]
        print(f"Reexecutando {len(pending)} execução(ões) com falha transitória.")
    started = time.monotonic()
    results = asyncio.run(
        run(
            settings,
            plan,
            delay=args.delay,
            retries=args.retries,
            retry_wait=args.retry_wait,
        )
    )
    if previous:
        retried = {(r.model, r.case): r for r in results}
        results = [retried.get((r.model, r.case), r) for r in previous]
    summaries = summarize(results)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = EVALS / "results" / f"{stamp}-{args.label}"
    write_json(
        folder / "results.json",
        {
            "created_at": stamp,
            "duration_s": round(time.monotonic() - started, 1),
            "network": settings.network.id,
            "cases": list(dict.fromkeys(r.case for r in results)),
            "summaries": [s.model_dump() for s in summaries],
            "runs": [r.model_dump() for r in results],
        },
    )
    report = render_markdown(results, summaries)
    (folder / "report.md").write_text(report, encoding="utf-8")
    print()
    print(report)
    print(f"Resultados em {folder.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
