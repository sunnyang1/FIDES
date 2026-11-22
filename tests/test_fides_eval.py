"""Regression tests for the FIDES-Eval v0.1 mechanism suites."""

from __future__ import annotations

from experiments.exp_fides_proxy_gauntlet import run_proxy_gauntlet
from experiments.exp_fides_valley_path import run_search, run_valley_path


def test_proxy_gauntlet_executes_governance_without_unsafe_promotion():
    result = run_proxy_gauntlet()

    assert result["status"] == "passed"
    assert result["metrics"]["cases"] == 8
    assert result["metrics"]["unsafe_promotions"] == 0
    assert result["metrics"]["honest_fix_accepted"] is True
    assert result["metrics"]["pre_execution_attacks_with_zero_sandbox_runs"] is True
    assert result["metrics"]["all_audit_chains_intact"] is True
    assert all(case["passed"] for case in result["cases"])

    reasons = {case["case"]: case.get("reason", "") for case in result["cases"]}
    assert "no strict dominance" in reasons["over_eager"]
    assert "no strict dominance" in reasons["public_name_specialisation"]
    assert "evaluator namespace" in reasons["evaluator_namespace_attack"]
    assert "commitment mismatch" in reasons["source_swap_after_commit"]
    assert "commitment mismatch" in reasons["held_out_rotation_after_commit"]
    assert "distinct signer DIDs" in reasons["colluding_board"]
    assert "approval signature invalid" in reasons["replayed_approval"]


def test_valley_path_discriminates_all_three_search_policies():
    result = run_valley_path()

    assert result["status"] == "passed"
    assert all(result["checks"].values())
    assert result["results"]["single_line"]["final_incumbent"] == "v0"
    assert result["results"]["ungoverned_archive"]["unsafe_promotions"] == 1
    assert result["results"]["governed_archive"]["unsafe_promotions"] == 0
    assert result["results"]["governed_archive"]["final_incumbent"] == "v2_context_aware_fix"


def test_valley_path_neutral_candidate_is_retained_without_promotion():
    result = run_search("governed_archive")
    bridge = next(
        event for event in result["events"] if event["candidate"] == "v1_structured_state"
    )

    assert bridge["outcome"] == "archived_neutral"
    assert result["archive_size"] == 3  # v0 + neutral bridge + final fix
    assert result["unsafe_promotions"] == 0
