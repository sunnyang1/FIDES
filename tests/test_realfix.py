"""Regression tests for deterministic RealFix selection and the real-Git harness."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict

import pytest

from experiments.exp_fides_realfix import run_realfix_smoke
from experiments.realfix import (
    RealFixError,
    RealFixTask,
    build_selection_manifest,
    load_tasks_jsonl,
    select_realfix_tasks,
)


def _task_records() -> list[dict[str, str]]:
    return [
        {
            "instance_id": f"owner{repo}__project{repo}-{number}",
            "repo": f"owner{repo}/project{repo}",
            "problem_statement": f"Issue {repo}-{number}",
            "base_commit": f"{repo:02d}{number:02d}" + "a" * 36,
            "patch": "gold solution must not enter the manifest",
            "test_patch": "held-out tests must not enter the manifest",
        }
        for repo in range(12)
        for number in range(4)
    ]


def test_realfix_30_selection_is_deterministic_and_repository_diverse(tmp_path):
    records = _task_records()
    source = tmp_path / "verified.jsonl"
    source.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    tasks = load_tasks_jsonl(source)

    selected = select_realfix_tasks(tasks)
    reversed_selection = select_realfix_tasks(list(reversed(tasks)))

    assert selected == reversed_selection
    assert len(selected) == 30
    assert len({task.instance_id for task in selected}) == 30
    assert sum(task.split == "dev" for task in selected) == 6
    assert sum(task.split == "held_out" for task in selected) == 24
    assert max(Counter(task.repo for task in selected).values()) <= 3

    manifest = build_selection_manifest(tasks, source)
    assert manifest["status"] == "selected_not_evaluated"
    assert manifest["selection"]["repository_count"] == 12
    assert manifest["source_sha256"]
    assert all("patch" not in task and "test_patch" not in task for task in manifest["tasks"])


def test_realfix_selector_rejects_duplicate_or_incomplete_exports(tmp_path):
    records = _task_records()[:2]
    source = tmp_path / "duplicate.jsonl"
    source.write_text(
        json.dumps(records[0]) + "\n" + json.dumps(records[0]) + "\n", encoding="utf-8"
    )
    with pytest.raises(RealFixError, match="duplicate instance_id"):
        load_tasks_jsonl(source)

    incomplete = RealFixTask.from_record(records[0], 1)
    with pytest.raises(RealFixError, match="requested 30 tasks"):
        select_realfix_tasks([incomplete])
    with pytest.raises(RealFixError, match="max_per_repo must be positive"):
        select_realfix_tasks([incomplete], size=1, max_per_repo=0)


def test_realfix_manifest_omits_gold_and_test_patches(tmp_path):
    source = tmp_path / "verified.jsonl"
    source.write_text("\n".join(json.dumps(record) for record in _task_records()), encoding="utf-8")
    manifest = build_selection_manifest(load_tasks_jsonl(source), source)
    serialized = json.dumps(manifest)

    assert "gold solution must not enter the manifest" not in serialized
    assert "held-out tests must not enter the manifest" not in serialized
    assert set(manifest["tasks"][0]) == set(asdict(RealFixTask.from_record(_task_records()[0], 1)))


def test_realfix_local_git_harness_rejects_tampering_and_accepts_reference_fix():
    result = run_realfix_smoke()

    assert result["status"] == "passed"
    assert all(result["checks"].values())
    assert result["baseline"]["status"] == "failed"
    assert result["cases"]["reference_fix"]["status"] == "passed"
    assert result["cases"]["wrong_fix"]["status"] == "failed"
    assert result["cases"]["protected_test_tampering"]["patch_applied"] is False
    assert result["cases"]["candidate_timeout"]["returncode"] == 124
    assert result["cases"]["baseline_timeout"]["status"] == "timeout"
    assert result["cases"]["candidate_runner_error"]["status"] == "error"
    assert result["cases"]["baseline_runner_error"]["status"] == "error"
    assert result["metrics"]["unsafe_test_tampering_applied"] == 0
    assert "not an official SWE-bench result" in result["boundary"]
