"""Opt-in live check: python backend/scripts/smoke_analysis.py HASH [--mode support]
[--config config/ethereum-mainnet.example.yaml] [--output result.json].

Without --config, uses the active profile (the same one the UI uses)."""

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config.loader import load_config
from app.domain.analysis import AnalyzeRequest
from app.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tx_hash")
    parser.add_argument("--mode", default="developer", choices=["developer", "support", "auditor"])
    parser.add_argument("--config", type=Path, help="network YAML (default: active profile)")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    request = AnalyzeRequest(tx_hash=args.tx_hash, mode=args.mode)
    config = load_config(args.config) if args.config else None
    with TestClient(create_app(config)) as client:
        response = client.post("/api/analyze", json=request.model_dump())
        response.raise_for_status()
        result = response.json()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "network",
                    "tx_hash",
                    "mode",
                    "status",
                    "summary",
                    "transaction",
                    "uncertainties",
                    "explanation_issue",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    explanation = result.get("explanation")
    if explanation:
        print("Explanation:", explanation.get("summary"))
    print("Evidence:", [source["id"] for source in result["sources"]])
    print(
        "Logs:",
        len(result["events"]),
        "Transfers:",
        len(result["transfers"]),
        "Calls:",
        len(result["calls"]),
    )


if __name__ == "__main__":
    main()
