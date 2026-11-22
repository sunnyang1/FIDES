"""Synthetic-fixture tests for RealFix plans and official result normalization."""

from __future__ import annotations

import json

import pytest

from experiments.realfix import RealFixError, build_selection_manifest, load_tasks_jsonl
from experiments.realfix_experiment import (
    ARMS,
    build_experiment_plan,
    compare_official_arms,
    derive_official_run_id,
    normalize_official_report,
    validate_predictions,
)
from tests.test_realfix import _task_records


def _manifest(tmp_path):
    source = tmp_path / "verified.jsonl"
    source.write_text("\n".join(json.dumps(record) for record in _task_records()), encoding="utf-8")
    return build_selection_manifest(load_tasks_jsonl(source), source)


def _official_result(manifest, resolved_count: int) -> dict:
    ids = [task["instance_id"] for task in manifest["tasks"]]
    resolved = ids[:resolved_count]
    unresolved = ids[resolved_count:-2]
    error = ids[-2:-1]
    empty = ids[-1:]
    completed = resolved + unresolved
    return {
        "schema_version": 2,
        "total_instances": 30,
        "submitted_instances": 30,
        "completed_instances": len(completed),
        "resolved_instances": len(resolved),
        "unresolved_instances": len(unresolved),
        "error_instances": len(error),
        "empty_patch_instances": len(empty),
        "infra_failure_instances": len(error),
        "ambiguous_failure_instances": 0,
        "submitted_ids": ids,
        "completed_ids": completed,
        "incomplete_ids": [],
        "resolved_ids": resolved,
        "unresolved_ids": unresolved,
        "error_ids": error,
        "empty_patch_ids": empty,
        "infra_failure_ids": error,
        "ambiguous_failure_ids": [],
        "failure_reasons": {error[0]: "synthetic fixture"},
    }


def _write_official_result(tmp_path, manifest, plan, arm, resolved_count=3, raw=None):
    predictions = tmp_path / f"{arm}-predictions.jsonl"
    predictions.write_text(
        "\n".join(
            json.dumps(
                {
                    "instance_id": task["instance_id"],
                    "model_name_or_path": "model-x",
                    "model_patch": "diff --git a/a.py b/a.py\n",
                }
            )
            for task in manifest["tasks"]
        ),
        encoding="utf-8",
    )
    run_id = derive_official_run_id(manifest, plan, arm, predictions)
    report_path = tmp_path / run_id / "results.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(raw if raw is not None else _official_result(manifest, resolved_count)),
        encoding="utf-8",
    )
    return report_path, predictions


def test_five_arm_plan_pins_common_budget_and_deterministic_run_ids(tmp_path):
    manifest = _manifest(tmp_path)
    plan = build_experiment_plan(manifest, "model-x", "scaffold-y", 7, 50_000, 12)
    repeated = build_experiment_plan(manifest, "model-x", "scaffold-y", 7, 50_000, 12)

    assert plan == repeated
    assert plan["status"] == "planned_not_run"
    assert [arm["arm"] for arm in plan["arms"]] == list(ARMS)
    assert len({arm["run_id_stem"] for arm in plan["arms"]}) == 5
    assert plan["common"]["budget_per_task"] == {
        "token_budget": 50_000,
        "model_call_budget": 12,
    }
    assert "not proof of equal consumption" in plan["budget_semantics"]
    with pytest.raises(RealFixError, match="not booleans"):
        build_experiment_plan(manifest, "model-x", "scaffold-y", True, 50_000, 12)


def test_prediction_validation_requires_exact_coverage_and_keeps_empty_patch(tmp_path):
    manifest = _manifest(tmp_path)
    predictions = tmp_path / "predictions.jsonl"
    records = [
        {
            "instance_id": task["instance_id"],
            "model_name_or_path": "model-x",
            "model_patch": "" if index == 0 else "diff --git a/a.py b/a.py\n",
        }
        for index, task in enumerate(manifest["tasks"])
    ]
    predictions.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )

    result = validate_predictions(predictions, manifest, "model-x")
    assert result["instance_count"] == 30
    assert result["empty_patch_ids"] == [manifest["tasks"][0]["instance_id"]]
    assert result["status"] == "valid_predictions_not_evaluated"

    predictions.write_text(
        "\n".join(json.dumps(record) for record in records[:-1]), encoding="utf-8"
    )
    with pytest.raises(RealFixError, match="coverage mismatch"):
        validate_predictions(predictions, manifest)


def test_official_report_normalization_and_five_arm_comparison(tmp_path):
    manifest = _manifest(tmp_path)
    plan = build_experiment_plan(manifest, "model-x", "scaffold-y", 7, 50_000, 12)
    normalized = []
    for index, arm in enumerate(ARMS):
        report_path, predictions = _write_official_result(
            tmp_path, manifest, plan, arm, resolved_count=index + 1
        )
        normalized.append(normalize_official_report(report_path, manifest, plan, arm, predictions))

    assert normalized[0]["status"] == "official_swebench_evaluated"
    assert normalized[0]["total"] == 30
    assert normalized[0]["resolved_rate"] == 1 / 30
    assert normalized[0]["split_rates"]["dev"]["total"] == 6
    assert normalized[0]["split_rates"]["held_out"]["total"] == 24

    comparison = compare_official_arms(normalized)
    assert comparison["status"] == "official_comparative_results"
    assert [arm["resolved"] for arm in comparison["arms"]] == [1, 2, 3, 4, 5]
    assert len(comparison["pairwise_resolved_differences"]) == 10
    assert "Multiple seeds" in comparison["boundary"]

    forged = dict(normalized[0], source_results_sha256="not-a-hash")
    with pytest.raises(RealFixError, match="source_results_sha256"):
        compare_official_arms([forged, *normalized[1:]])


def test_official_report_rejects_inconsistent_counts_and_cross_arm_manifests(tmp_path):
    manifest = _manifest(tmp_path)
    plan = build_experiment_plan(manifest, "model-x", "scaffold-y", 7, 50_000, 12)
    raw = _official_result(manifest, 3)
    raw["resolved_instances"] = 4
    report_path, predictions = _write_official_result(tmp_path, manifest, plan, "base", raw=raw)
    with pytest.raises(RealFixError, match="resolved_instances"):
        normalize_official_report(report_path, manifest, plan, "base", predictions)

    valid_path, predictions = _write_official_result(tmp_path, manifest, plan, "base")
    normalized = normalize_official_report(valid_path, manifest, plan, "base", predictions)
    with pytest.raises(RealFixError, match="for this arm and patch"):
        normalize_official_report(valid_path, manifest, plan, "iterative", predictions)

    legacy = _official_result(manifest, 3)
    for field in (
        "infra_failure_instances",
        "ambiguous_failure_instances",
        "infra_failure_ids",
        "ambiguous_failure_ids",
    ):
        legacy.pop(field)
    valid_path.write_text(json.dumps(legacy), encoding="utf-8")
    assert (
        normalize_official_report(valid_path, manifest, plan, "base", predictions)["counts"][
            "infra_failure_instances"
        ]
        == 0
    )

    incomplete = _official_result(manifest, 3)
    incomplete["error_ids"] = []
    incomplete["error_instances"] = 0
    valid_path.write_text(json.dumps(incomplete), encoding="utf-8")
    with pytest.raises(RealFixError, match="submitted IDs must equal"):
        normalize_official_report(valid_path, manifest, plan, "base", predictions)

    mismatched_arms = [
        dict(
            normalized,
            arm=arm,
            manifest_sha256="different" if arm == "iterative" else normalized["manifest_sha256"],
        )
        for arm in ARMS
    ]
    with pytest.raises(RealFixError, match="same manifest"):
        compare_official_arms(mismatched_arms)

    mismatched_configs = [
        dict(
            normalized,
            arm=arm,
            common=dict(
                normalized["common"], scaffold="other" if arm == "greedy" else "scaffold-y"
            ),
        )
        for arm in ARMS
    ]
    with pytest.raises(RealFixError, match="same model, scaffold, seed, and budgets"):
        compare_official_arms(mismatched_configs)
