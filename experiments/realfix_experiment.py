"""RealFix-30 experiment plans, prediction validation, and official report comparison.

No function in this module calls a model or claims to run SWE-bench. Official
outcomes are accepted only from the SWE-bench ``results.json`` schema.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from experiments.realfix import RealFixError

ARMS = ("base", "iterative", "greedy", "fides_single_line", "fides_full_archive")
PLAN_VERSION = "realfix-experiment-plan-v1"
NORMALIZER_VERSION = "realfix-official-normalizer-v1"


def _read_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RealFixError(f"invalid JSON in {path}: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise RealFixError(f"expected a JSON object in {path}")
    return value


def _load_manifest(manifest: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    value = _read_json(manifest) if isinstance(manifest, (str, Path)) else dict(manifest)
    tasks = value.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 30:
        raise RealFixError("RealFix experiment requires exactly 30 manifest tasks")
    tasks = [task for task in value["tasks"] if isinstance(task, dict)]
    ids = [task.get("instance_id") for task in tasks]
    if len(ids) != 30 or any(not isinstance(iid, str) or not iid for iid in ids):
        raise RealFixError("manifest contains an invalid instance_id")
    if len(set(ids)) != 30:
        raise RealFixError("manifest instance IDs must be unique")
    if any(task.get("split") not in {"dev", "held_out"} for task in tasks):
        raise RealFixError("every manifest task must have a dev or held_out split")
    return value


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    """Commit to the selected task identities and splits, not local file paths."""
    tasks = [
        {"instance_id": task["instance_id"], "split": task["split"]} for task in manifest["tasks"]
    ]
    payload = json.dumps(tasks, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_plan(plan: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    value = _read_json(plan) if isinstance(plan, (str, Path)) else dict(plan)
    if value.get("plan_version") != PLAN_VERSION or not isinstance(value.get("common"), dict):
        raise RealFixError("invalid RealFix experiment plan")
    common = value["common"]
    if any(
        not isinstance(common.get(field), str) or not common[field]
        for field in ("model_name_or_path", "scaffold")
    ):
        raise RealFixError("experiment plan must pin model and scaffold")
    if not isinstance(common.get("seed"), int) or isinstance(common["seed"], bool):
        raise RealFixError("experiment plan must pin an integer seed")
    if not isinstance(common.get("budget_per_task"), dict):
        raise RealFixError("experiment plan must pin per-task budgets")
    for field in ("token_budget", "model_call_budget"):
        budget = common["budget_per_task"].get(field)
        if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
            raise RealFixError(f"experiment plan must pin a positive {field}")
    if not isinstance(value.get("arms"), list):
        raise RealFixError("experiment plan has no arms")
    return value


def _plan_arm(plan: Mapping[str, Any], arm: str) -> Mapping[str, Any]:
    matches = [
        entry for entry in plan["arms"] if isinstance(entry, dict) and entry.get("arm") == arm
    ]
    if (
        len(matches) != 1
        or not isinstance(matches[0].get("run_id_stem"), str)
        or not matches[0]["run_id_stem"]
    ):
        raise RealFixError(f"experiment plan does not define exactly one {arm} arm")
    return matches[0]


def build_experiment_plan(
    manifest: str | Path | Mapping[str, Any],
    model_name_or_path: str,
    scaffold: str,
    seed: int,
    token_budget: int,
    model_call_budget: int,
) -> dict[str, Any]:
    """Create a five-arm configuration with identical tasks and budget caps."""
    selected = _load_manifest(manifest)
    if not model_name_or_path.strip() or not scaffold.strip():
        raise RealFixError("model and scaffold must be non-empty")
    numeric_values = (seed, token_budget, model_call_budget)
    if any(isinstance(value, bool) for value in numeric_values):
        raise RealFixError("seed and budgets must be integers, not booleans")
    if seed < 0 or token_budget <= 0 or model_call_budget <= 0:
        raise RealFixError("seed must be non-negative and budgets must be positive")
    digest = manifest_digest(selected)
    budget = {"token_budget": token_budget, "model_call_budget": model_call_budget}
    common = {
        "model_name_or_path": model_name_or_path,
        "scaffold": scaffold,
        "seed": seed,
        "budget_per_task": budget,
    }
    run_stem = hashlib.sha256(
        json.dumps([digest, common], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return {
        "suite": "FIDES-Eval RealFix-30 controlled comparison",
        "plan_version": PLAN_VERSION,
        "status": "planned_not_run",
        "manifest_sha256": digest,
        "task_ids": [task["instance_id"] for task in selected["tasks"]],
        "common": common,
        "budget_semantics": (
            "Equal per-task caps, not proof of equal consumption. Record actual tokens, "
            "model calls, wall time, and cost for every arm."
        ),
        "arms": [
            {
                "arm": arm,
                "run_id_stem": f"realfix30-{run_stem}-{arm}-seed{seed}",
                "predictions_file": f"predictions/{arm}.jsonl",
                "official_report": (
                    f"logs/evaluation/realfix30-{run_stem}-{arm}-seed{seed}-"
                    "<prediction-sha256-prefix>/results.json"
                ),
            }
            for arm in ARMS
        ],
        "boundary": "Configuration only; no predictions were generated and no evaluation was run.",
    }


def validate_predictions(
    predictions_path: str | Path,
    manifest: str | Path | Mapping[str, Any],
    expected_model: str | None = None,
) -> dict[str, Any]:
    """Validate official prediction JSONL coverage, identity, and field types.

    Empty patches are retained because SWE-bench grades them as empty-patch
    outcomes. Rejecting them here would hide a legitimate model failure mode.
    """
    selected = _load_manifest(manifest)
    expected_ids = {task["instance_id"] for task in selected["tasks"]}
    seen: set[str] = set()
    models: set[str] = set()
    empty_patch_ids: list[str] = []
    with Path(predictions_path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RealFixError(f"line {line_number}: invalid prediction JSON") from exc
            if not isinstance(record, dict):
                raise RealFixError(f"line {line_number}: prediction must be an object")
            for field in ("instance_id", "model_name_or_path", "model_patch"):
                if not isinstance(record.get(field), str):
                    raise RealFixError(f"line {line_number}: {field} must be a string")
            iid = record["instance_id"]
            if iid in seen:
                raise RealFixError(f"line {line_number}: duplicate prediction {iid}")
            seen.add(iid)
            models.add(record["model_name_or_path"])
            if not record["model_patch"]:
                empty_patch_ids.append(iid)
    missing = sorted(expected_ids - seen)
    extra = sorted(seen - expected_ids)
    if missing or extra:
        raise RealFixError(f"prediction coverage mismatch; missing={missing}, extra={extra}")
    if len(models) != 1:
        raise RealFixError("all predictions must use one model_name_or_path")
    model = next(iter(models))
    if expected_model is not None and model != expected_model:
        raise RealFixError(f"expected model {expected_model!r}, got {model!r}")
    return {
        "status": "valid_predictions_not_evaluated",
        "instance_count": len(seen),
        "model_name_or_path": model,
        "empty_patch_ids": sorted(empty_patch_ids),
        "predictions_sha256": hashlib.sha256(Path(predictions_path).read_bytes()).hexdigest(),
        "manifest_sha256": manifest_digest(selected),
        "boundary": "Schema and coverage validation only; no patch quality or score is implied.",
    }


def _ids(report: Mapping[str, Any], field: str) -> set[str]:
    value = report.get(field, [])
    if not isinstance(value, list) or any(not isinstance(iid, str) for iid in value):
        raise RealFixError(f"official report field {field} must be a string list")
    if len(value) != len(set(value)):
        raise RealFixError(f"official report field {field} contains duplicates")
    return set(value)


def _check_count(report: Mapping[str, Any], field: str, expected: int) -> None:
    value = report.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value != expected:
        raise RealFixError(f"official report {field}={value!r}; expected {expected}")


def derive_official_run_id(
    manifest: str | Path | Mapping[str, Any],
    plan: str | Path | Mapping[str, Any],
    arm: str,
    predictions_path: str | Path,
) -> str:
    """Bind the cache-sensitive official run ID to arm and exact predictions."""
    selected = _load_manifest(manifest)
    experiment = _load_plan(plan)
    if experiment.get("manifest_sha256") != manifest_digest(selected):
        raise RealFixError("experiment plan and task manifest do not match")
    if experiment.get("task_ids") != [task["instance_id"] for task in selected["tasks"]]:
        raise RealFixError("experiment plan task IDs do not match the manifest")
    arm_config = _plan_arm(experiment, arm)
    validated = validate_predictions(
        predictions_path, selected, experiment["common"]["model_name_or_path"]
    )
    return f"{arm_config['run_id_stem']}-{validated['predictions_sha256'][:12]}"


def normalize_official_report(
    report_path: str | Path,
    manifest: str | Path | Mapping[str, Any],
    plan: str | Path | Mapping[str, Any],
    arm: str,
    predictions_path: str | Path,
) -> dict[str, Any]:
    """Validate and normalize one official SWE-bench schema-v2 results file."""
    if arm not in ARMS:
        raise RealFixError(f"unknown arm {arm!r}")
    selected = _load_manifest(manifest)
    experiment = _load_plan(plan)
    run_id = derive_official_run_id(selected, experiment, arm, predictions_path)
    source = Path(report_path)
    if source.name != "results.json" or source.parent.name != run_id:
        raise RealFixError(
            f"official report path must be <run-dir>/{run_id}/results.json for this arm and patch"
        )
    validated_predictions = validate_predictions(
        predictions_path, selected, experiment["common"]["model_name_or_path"]
    )
    report = _read_json(report_path)
    if report.get("schema_version") != 2:
        raise RealFixError("only official SWE-bench results schema_version 2 is supported")
    expected_ids = {task["instance_id"] for task in selected["tasks"]}
    submitted = _ids(report, "submitted_ids")
    completed = _ids(report, "completed_ids")
    resolved = _ids(report, "resolved_ids")
    unresolved = _ids(report, "unresolved_ids")
    errors = _ids(report, "error_ids")
    empty = _ids(report, "empty_patch_ids")
    incomplete = _ids(report, "incomplete_ids")
    infra = _ids(report, "infra_failure_ids")
    ambiguous = _ids(report, "ambiguous_failure_ids")

    if submitted != expected_ids or incomplete:
        raise RealFixError("official report does not cover exactly the 30 selected tasks")
    for field, ids in {
        "completed_ids": completed,
        "resolved_ids": resolved,
        "unresolved_ids": unresolved,
        "error_ids": errors,
        "empty_patch_ids": empty,
        "infra_failure_ids": infra,
        "ambiguous_failure_ids": ambiguous,
    }.items():
        if not ids <= expected_ids:
            raise RealFixError(f"official report {field} contains unselected task IDs")
    if not resolved <= completed or not unresolved <= completed:
        raise RealFixError("resolved and unresolved IDs must be completed instances")
    if completed & errors or completed & empty or errors & empty:
        raise RealFixError("completed, error, and empty-patch outcomes must be disjoint")
    if submitted != completed | errors | empty:
        raise RealFixError("submitted IDs must equal completed plus error plus empty-patch IDs")
    if resolved & unresolved:
        raise RealFixError("resolved and unresolved IDs overlap")
    if completed != resolved | unresolved:
        raise RealFixError("completed IDs must equal resolved plus unresolved IDs")
    if not (infra | ambiguous) <= (unresolved | errors):
        raise RealFixError("failure classifications must refer to unresolved or error tasks")

    counts = {
        "total_instances": 30,
        "submitted_instances": 30,
        "completed_instances": len(completed),
        "resolved_instances": len(resolved),
        "unresolved_instances": len(unresolved),
        "error_instances": len(errors),
        "empty_patch_instances": len(empty),
        "infra_failure_instances": len(infra),
        "ambiguous_failure_instances": len(ambiguous),
    }
    for field, expected in counts.items():
        if (
            field in {"infra_failure_instances", "ambiguous_failure_instances"}
            and field not in report
            and expected == 0
        ):
            # These additive diagnostics are absent in older schema-v2 reports.
            continue
        _check_count(report, field, expected)

    split_rates: dict[str, Any] = {}
    for split in ("dev", "held_out"):
        split_ids = {task["instance_id"] for task in selected["tasks"] if task["split"] == split}
        split_rates[split] = {
            "total": len(split_ids),
            "resolved": len(resolved & split_ids),
            "resolved_rate": len(resolved & split_ids) / len(split_ids),
        }
    return {
        "normalizer_version": NORMALIZER_VERSION,
        "status": "official_swebench_evaluated",
        "arm": arm,
        "run_id": run_id,
        "common": experiment["common"],
        "manifest_sha256": manifest_digest(selected),
        "source_results_sha256": hashlib.sha256(Path(report_path).read_bytes()).hexdigest(),
        "predictions_sha256": validated_predictions["predictions_sha256"],
        "total": 30,
        "resolved": len(resolved),
        "resolved_rate": len(resolved) / 30,
        "resolved_ids": sorted(resolved),
        "unresolved_ids": sorted(unresolved),
        "error_ids": sorted(errors),
        "empty_patch_ids": sorted(empty),
        "infra_failure_ids": sorted(infra),
        "ambiguous_failure_ids": sorted(ambiguous),
        "split_rates": split_rates,
        "counts": counts,
        "boundary": "Normalized from an official results.json; no statistical significance is implied.",
    }


def compare_official_arms(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Compare all five arms on identical tasks without inventing a ranking."""
    if {result.get("arm") for result in results} != set(ARMS) or len(results) != len(ARMS):
        raise RealFixError("comparison requires exactly one normalized result for each arm")
    digests = {result.get("manifest_sha256") for result in results}
    if len(digests) != 1:
        raise RealFixError("all arms must use the same manifest")
    common_configs = {
        json.dumps(result.get("common"), sort_keys=True, separators=(",", ":"))
        for result in results
    }
    if len(common_configs) != 1:
        raise RealFixError("all arms must use the same model, scaffold, seed, and budgets")
    if any(result.get("status") != "official_swebench_evaluated" for result in results):
        raise RealFixError("only normalized official SWE-bench results can be compared")
    for result in results:
        if result.get("normalizer_version") != NORMALIZER_VERSION:
            raise RealFixError("comparison requires the current normalizer version")
        for field in ("source_results_sha256", "predictions_sha256"):
            digest = result.get(field)
            if not isinstance(digest, str) or len(digest) != 64:
                raise RealFixError(f"normalized result lacks a valid {field}")
            try:
                int(digest, 16)
            except ValueError as exc:
                raise RealFixError(f"normalized result lacks a valid {field}") from exc
        run_id = result.get("run_id")
        if not isinstance(run_id, str) or not run_id.endswith(result["predictions_sha256"][:12]):
            raise RealFixError("normalized run_id is not bound to its predictions")
        resolved_ids = result.get("resolved_ids")
        if (
            not isinstance(resolved_ids, list)
            or any(not isinstance(iid, str) for iid in resolved_ids)
            or len(resolved_ids) != len(set(resolved_ids))
            or result.get("resolved") != len(resolved_ids)
            or result.get("total") != 30
            or result.get("resolved_rate") != len(resolved_ids) / 30
        ):
            raise RealFixError("normalized resolved counts and IDs are inconsistent")
    if len({result["run_id"] for result in results}) != len(ARMS):
        raise RealFixError("each arm must have a distinct official run_id")

    arms = {result["arm"]: dict(result) for result in results}
    resolved_sets = {arm: set(result["resolved_ids"]) for arm, result in arms.items()}
    pairwise = []
    for left_index, left in enumerate(ARMS):
        for right in ARMS[left_index + 1 :]:
            pairwise.append(
                {
                    "left": left,
                    "right": right,
                    "only_left": sorted(resolved_sets[left] - resolved_sets[right]),
                    "only_right": sorted(resolved_sets[right] - resolved_sets[left]),
                }
            )
    return {
        "suite": "FIDES-Eval RealFix-30 controlled comparison",
        "status": "official_comparative_results",
        "manifest_sha256": next(iter(digests)),
        "common": results[0]["common"],
        "arms": [
            {
                "arm": arm,
                "run_id": arms[arm]["run_id"],
                "resolved": arms[arm]["resolved"],
                "resolved_rate": arms[arm]["resolved_rate"],
                "split_rates": arms[arm]["split_rates"],
            }
            for arm in ARMS
        ],
        "pairwise_resolved_differences": pairwise,
        "boundary": (
            "Observed task-level differences only. Multiple seeds, actual cost/latency, "
            "and uncertainty analysis are required before claiming improvement."
        ),
    }
