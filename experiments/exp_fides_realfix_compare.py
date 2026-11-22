"""Configure and normalize a controlled RealFix-30 comparison.

This CLI never generates model predictions or runs the official harness.
Run ``python3 experiments/exp_fides_realfix_compare.py --help`` for commands.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.realfix_experiment import (
    ARMS,
    build_experiment_plan,
    compare_official_arms,
    derive_official_run_id,
    normalize_official_report,
    validate_predictions,
)


def _write_json(value: Any, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="write an identical-budget five-arm plan")
    plan.add_argument("--manifest", required=True)
    plan.add_argument("--model", required=True)
    plan.add_argument("--scaffold", required=True)
    plan.add_argument("--seed", type=int, required=True)
    plan.add_argument("--token-budget", type=int, required=True)
    plan.add_argument("--model-call-budget", type=int, required=True)
    plan.add_argument("--output", type=Path, required=True)

    validate = subparsers.add_parser("validate", help="validate prediction JSONL coverage")
    validate.add_argument("--predictions", required=True)
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--expected-model")
    validate.add_argument("--output", type=Path)

    normalize = subparsers.add_parser("normalize", help="normalize official results.json")
    normalize.add_argument("--report", required=True)
    normalize.add_argument("--manifest", required=True)
    normalize.add_argument("--plan", required=True, help="experiment plan used for this run")
    normalize.add_argument("--arm", choices=ARMS, required=True)
    normalize.add_argument("--predictions", required=True, help="exact evaluated prediction JSONL")
    normalize.add_argument("--output", type=Path, required=True)

    run_id = subparsers.add_parser("run-id", help="derive a patch-bound official run ID")
    run_id.add_argument("--manifest", required=True)
    run_id.add_argument("--plan", required=True)
    run_id.add_argument("--arm", choices=ARMS, required=True)
    run_id.add_argument("--predictions", required=True)

    compare = subparsers.add_parser("compare", help="compare five normalized official arms")
    compare.add_argument("--results", nargs=5, required=True)
    compare.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "plan":
        result = build_experiment_plan(
            args.manifest,
            args.model,
            args.scaffold,
            args.seed,
            args.token_budget,
            args.model_call_budget,
        )
        _write_json(result, args.output)
        print("RealFix-30 five-arm plan: PLANNED (not run)")
    elif args.command == "validate":
        result = validate_predictions(args.predictions, args.manifest, args.expected_model)
        if args.output:
            _write_json(result, args.output)
        print(f"Prediction coverage: VALID ({result['instance_count']} tasks, not evaluated)")
    elif args.command == "normalize":
        result = normalize_official_report(
            args.report, args.manifest, args.plan, args.arm, args.predictions
        )
        _write_json(result, args.output)
        print(f"Official {args.arm} report normalized: {result['resolved']}/30 resolved")
    elif args.command == "run-id":
        print(derive_official_run_id(args.manifest, args.plan, args.arm, args.predictions))
    else:
        result = compare_official_arms(
            [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.results]
        )
        _write_json(result, args.output)
        print("Five-arm official comparison normalized; see the output for task-level differences")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
