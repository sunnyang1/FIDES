"""FIDES Phase 1 tests — the governed evolution loop (Anchor tournament)."""

from __future__ import annotations

import pytest

from adl_lite.evolution.engine import (
    ConstitutionError,
    EvolutionEngine,
    MockProposer,
    check_constitution,
)
from adl_lite.maat import AnchorSpec


def test_constitution_blocks_evaluator_blinding():
    """DGM incident: candidate removes the detector's input channel."""
    bad = AnchorSpec()
    bad.required_payload_fields = {"task_validate": [], "task_submit": ["result_ref"]}
    with pytest.raises(ConstitutionError):
        check_constitution(bad)


def test_constitution_blocks_all_gates_off():
    bad = AnchorSpec(
        enforce_identity=True, enforce_lineage=False, enforce_circuit_breaker=False
    )
    with pytest.raises(ConstitutionError):
        check_constitution(bad)


def test_constitution_blocks_lineage_off():
    bad = AnchorSpec(enforce_lineage=False)
    with pytest.raises(ConstitutionError):
        check_constitution(bad)


def test_engine_genesis_evaluates_incumbent():
    eng = EvolutionEngine()
    assert len(eng.lineage) == 1
    card = eng.lineage[0].scorecard
    assert card.normal_completed
    # incumbent anchor guards prod-db? default anchor has no shared resources,
    # so the contention scenario's block expectation depends on config; the
    # default card simply records what happened deterministically.
    assert card.all_integrity
    assert eng.anchor_chain.verify()


def test_full_loop_accept_dominating_candidate():
    """A candidate that adds prod-db to shared_resources must dominate the
    default anchor on the contention + pingpong scenarios."""
    eng = EvolutionEngine()
    incumbent_safety = eng.lineage[-1].scorecard.safety_violations_caught
    better = eng.lineage[-1].spec.model_copy(
        update={"version": 2, "shared_resources": ["prod-db"]}
    )
    card = eng.evaluate_candidate(better)
    assert card.safety_violations_caught > incumbent_safety
    eng.accept(better, card, actor="did:key:zRoot")
    assert eng.lineage[-1].version == 2
    assert eng.anchor_chain.verify()
    assert eng.report()[-1]["accepted"] is True


def test_accept_refuses_non_dominating_candidate():
    eng = EvolutionEngine()
    worse = eng.lineage[-1].spec.model_copy(update={"version": 2, "rework_threshold": 1})
    card = eng.evaluate_candidate(worse)
    # same safety catches, same normal completion -> not strict dominance
    with pytest.raises(ConstitutionError):
        eng.accept(worse, card, actor="did:key:zRoot")
    # lineage unchanged
    assert len(eng.lineage) == 1


def test_rollback_is_a_fork_not_a_deletion():
    eng = EvolutionEngine()
    base = eng.lineage[-1].spec
    v2 = base.model_copy(update={"version": 2, "shared_resources": ["prod-db"]})
    card2 = eng.evaluate_candidate(v2)
    eng.accept(v2, card2, actor="did:key:zRoot")
    fork = eng.rollback(to_version=1, actor="did:key:zRoot")
    assert fork.version == 3  # new revision, not a rewind
    assert eng.lineage[-1].spec.shared_resources == base.shared_resources
    assert eng.lineage[-1].parent_version == 1
    # full history intact — the detour is recorded, not erased
    assert [e.version for e in eng.lineage] == [1, 2, 3]
    assert eng.anchor_chain.verify()


def test_budget_forces_escalation():
    eng = EvolutionEngine(budget=2)
    eng.propose_candidate(); eng._generations = 2
    with pytest.raises(ConstitutionError):
        eng.propose_candidate()


def test_mock_proposer_variants_stay_constitutional():
    eng = EvolutionEngine(proposer=MockProposer(seed=7))
    for _ in range(6):
        cand = eng.propose_candidate()
        check_constitution(cand)  # never raises
        eng._generations += 0  # evaluate/accept not required for this test
