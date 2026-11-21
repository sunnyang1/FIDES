"""FIDES Phase 2 tests — artifact evolution: replay, commit–reveal held-out
bench, acceptance routing (diff risk × reputation), lineage budgets."""

from __future__ import annotations

import pytest

from adl_lite.agents.task import TaskStatus
from adl_lite.evolution.replay import (
    HIGH_RISK_FIELDS,
    LOW_RISK_FIELDS,
    MEDIUM_RISK_FIELDS,
    ArtifactEvolutionEngine,
    ArtifactLineage,
    HeldOutBench,
    PlannerSpec,
    Step,
    TaskTrace,
    acceptance_route,
    diff_risk,
    plan,
    sample_traces,
    score_plans,
)


def _escalation_miss_trace(name: str = "hidden_dispute", loops: int = 3) -> TaskTrace:
    """A trace the DEFAULT spec (rework_hard_stop=3) misses: loops reach the
    hard stop but the close outcome is 'accepted' instead of 'cancelled'."""
    steps = []
    for _ in range(loops + 1):
        steps.append(Step("claim", "worker", {"agent_did": "did:key:zW"}))
        steps.append(Step("submit", "worker", {"result_ref": "r"}))
        if _ < loops:
            steps.append(Step("validate", "reviewer", {"accepted": False}))
    steps.append(Step("validate", "reviewer", {"accepted": True}))
    steps.append(Step("close", "planner", {"outcome": "accepted"}))
    return TaskTrace(name, "dispute", None, steps, TaskStatus.CLOSED)


# ---------------------------------------------------------------------------
# Replay + scoring
# ---------------------------------------------------------------------------


def test_public_traces_all_clean_under_default_spec():
    spec = PlannerSpec()
    for t in sample_traces():
        r = score_plans(t, spec)
        assert r["clean"], f"{t.name} should be clean under defaults"


def test_missed_escalation_detected_when_loops_hit_hard_stop():
    trace = _escalation_miss_trace()
    # default hard_stop=3, loops=3, non-cancelled close -> miss injected
    r = score_plans(trace, PlannerSpec())
    assert r["missed_escalations"] == 1
    assert not r["clean"]
    # raising the hard stop above the loop count clears the miss
    r2 = score_plans(trace, PlannerSpec(rework_hard_stop=10))
    assert r2["missed_escalations"] == 0
    assert r2["clean"]


def test_plan_is_deterministic():
    trace = sample_traces()[1]
    assert plan(trace, PlannerSpec()) == plan(trace, PlannerSpec())


# ---------------------------------------------------------------------------
# Commit–reveal held-out bench
# ---------------------------------------------------------------------------


def test_seal_and_verify_commitment():
    bench = HeldOutBench(held_out=[_escalation_miss_trace()])
    candidates = [PlannerSpec(version=2)]
    commitment = bench.seal(candidates)
    assert commitment and bench.verify_commitment(candidates)


def test_tampered_candidate_breaks_commitment():
    bench = HeldOutBench(held_out=[_escalation_miss_trace()])
    bench.seal([PlannerSpec(version=2)])
    assert not bench.verify_commitment([PlannerSpec(version=3)])


def test_tampered_held_out_breaks_commitment():
    bench = HeldOutBench(held_out=[_escalation_miss_trace()])
    bench.seal([PlannerSpec(version=2)])
    bench.held_out = [_escalation_miss_trace("other")]
    assert not bench.verify_commitment([PlannerSpec(version=2)])


def test_reveal_marks_bench_revealed():
    bench = HeldOutBench(held_out=[_escalation_miss_trace()])
    out = bench.reveal()
    assert bench.revealed and len(out) == 1


# ---------------------------------------------------------------------------
# Diff risk + acceptance routing
# ---------------------------------------------------------------------------


def test_diff_risk_classification():
    base = PlannerSpec()
    assert diff_risk(base, PlannerSpec(w_claim=9.0)) == "low"
    assert diff_risk(base, PlannerSpec(rework_hard_stop=5)) == "medium"
    assert diff_risk(base, PlannerSpec(name="rogue")) == "constitutional"
    # high risk dominates even when combined with low-risk changes
    assert diff_risk(base, PlannerSpec(name="rogue", w_claim=9.0)) == "constitutional"


def test_risk_field_partitions_cover_all_spec_fields():
    covered = LOW_RISK_FIELDS | MEDIUM_RISK_FIELDS | HIGH_RISK_FIELDS
    spec_fields = {f for f in PlannerSpec.__dataclass_fields__ if f not in ("version",)}
    assert covered == spec_fields


def test_acceptance_route_matrix():
    assert acceptance_route("constitutional", 0.99) == "board_multi_signoff"
    assert acceptance_route("low", 0.95) == "auto_accept"
    assert acceptance_route("medium", 0.95) == "single_reviewer"
    assert acceptance_route("low", 0.5) == "board"
    # constitutional is never delegable, whatever the reputation
    assert acceptance_route("constitutional", 1.0) == "board_multi_signoff"


# ---------------------------------------------------------------------------
# Lineage budget + trust
# ---------------------------------------------------------------------------


def test_budget_exhaustion():
    lin = ArtifactLineage(PlannerSpec(), None, budget=2)
    lin.spend()
    assert not lin.exhausted
    lin.spend()
    assert lin.exhausted and lin.generations == 2


def test_trust_ewma_updates():
    lin = ArtifactLineage(PlannerSpec(), None)  # trust 0.5
    lin.record_outcome(accepted=True)
    assert lin.trust_score == 0.6
    lin.record_outcome(accepted=False)
    assert lin.trust_score == pytest.approx(0.48)


# ---------------------------------------------------------------------------
# Engine end-to-end
# ---------------------------------------------------------------------------


def _engine_with_hidden_dispute() -> ArtifactEvolutionEngine:
    eng = ArtifactEvolutionEngine(
        bench=HeldOutBench(held_out=[_escalation_miss_trace()])
    )
    return eng


def test_engine_evaluate_verifies_seal():
    eng = _engine_with_hidden_dispute()
    candidate = eng.propose()
    card = eng.evaluate(candidate)
    assert card.commitment_ok
    assert card.candidate_version == candidate.version


def test_engine_full_acceptance_cycle():
    eng = _engine_with_hidden_dispute()
    # n=1 tweak: rework_hard_stop 3 -> 2 (medium risk) — still misses the
    # hidden trace; n=2: w_shared; n=3: w_rework; n=4: hard_stop 3->2...
    # Directly craft the dominating candidate instead of walking the seed.
    candidate = PlannerSpec(version=2, rework_hard_stop=10)
    card = eng.evaluate(candidate)
    route = eng.accept(candidate, card)
    # trust 0.5 + medium risk -> board
    assert route == "board"
    assert card.dominates_incumbent
    assert eng.lineage[-1].artifact.version == 2
    assert eng.bench.revealed
    # audit trail records the decision
    entry = eng.audit_log[-1]
    assert entry["outcome"] == "accepted" and entry["route"] == "board"


def test_accept_rejects_candidate_that_fails_on_held_out():
    eng = _engine_with_hidden_dispute()
    candidate = PlannerSpec(version=2, rework_hard_stop=2)  # still misses
    card = eng.evaluate(candidate)
    with pytest.raises(ValueError, match="does not dominate"):
        eng.accept(candidate, card)
    # lineage unchanged, trust down-weighted, re-sealed for next generation
    assert eng.lineage[-1].artifact.version == 1
    assert eng.lineage[-1].trust_score < 0.5
    assert eng.audit_log[-1]["outcome"] == "rejected"
    # next generation can proceed against the re-sealed bench
    better = PlannerSpec(version=3, rework_hard_stop=10)
    card2 = eng.evaluate(better)
    assert card2.commitment_ok
    eng.accept(better, card2)
    assert eng.lineage[-1].artifact.version == 3


def test_broken_commitment_blocked_at_acceptance():
    eng = _engine_with_hidden_dispute()
    candidate = PlannerSpec(version=2, rework_hard_stop=10)
    card = eng.evaluate(candidate)
    card.commitment_ok = False  # simulate seal tampering discovered late
    with pytest.raises(ValueError, match="seal broken"):
        eng.accept(candidate, card)


def test_delegation_never_waives_dominance_gate():
    """High trust + low-risk diff would route to auto_accept, but a tied
    candidate (no strict improvement) must be downgraded and rejected."""
    eng = _engine_with_hidden_dispute()
    eng.lineage[-1].trust_score = 0.95
    # low-risk knob change only; incumbent and candidate tie on the hidden
    # trace (both miss with hard_stop=3, loops=3)
    candidate = PlannerSpec(version=2, w_shared=5.0)
    card = eng.evaluate(candidate)
    with pytest.raises(ValueError, match="does not dominate"):
        eng.accept(candidate, card)
    assert eng.audit_log[-1]["route"] == "board"


def test_double_accept_same_generation_blocked():
    eng = _engine_with_hidden_dispute()
    candidate = PlannerSpec(version=2, rework_hard_stop=10)
    card = eng.evaluate(candidate)
    eng.accept(candidate, card)
    card2 = eng.evaluate(PlannerSpec(version=3, rework_hard_stop=11))
    with pytest.raises(ValueError, match="already revealed"):
        eng.accept(PlannerSpec(version=3, rework_hard_stop=11), card2)


def test_constitutional_change_requires_multi_signoff():
    eng = _engine_with_hidden_dispute()
    eng.lineage[-1].trust_score = 0.99
    candidate = PlannerSpec(version=2, name="renamed", rework_hard_stop=10)
    card = eng.evaluate(candidate)
    route = eng.accept(candidate, card)
    assert route == "board_multi_signoff"


def test_propose_raises_when_budget_exhausted():
    eng = _engine_with_hidden_dispute()
    eng.lineage[-1].budget = 1
    eng.lineage[-1].spend()
    assert eng.lineage[-1].exhausted
    with pytest.raises(ValueError, match="budget exhausted"):
        eng.propose()


def test_constitution_rejects_sub_one_hard_stop():
    eng = _engine_with_hidden_dispute()
    with pytest.raises(ValueError, match="constitution"):
        eng.evaluate(PlannerSpec(version=2, rework_hard_stop=0))
    with pytest.raises(ValueError):
        ArtifactEvolutionEngine(base_artifact=PlannerSpec(rework_hard_stop=0))


def test_constitution_couples_hard_stop_below_anchor_threshold():
    """Experiment P2 finding: the circuit breaker caps observable rework
    loops at threshold-1, so a hard_stop >= threshold is a dead knob and
    must be rejected before evaluation."""
    bench = HeldOutBench(held_out=[])
    eng = ArtifactEvolutionEngine(bench=bench, rework_threshold=5)
    # 4 < 5: constitutional
    ok = PlannerSpec(version=2, rework_hard_stop=4)
    eng.evaluate(ok)
    # 5 >= 5: stop condition unreachable — constitution violation
    with pytest.raises(ValueError, match="unreachable"):
        eng.evaluate(PlannerSpec(version=3, rework_hard_stop=5))
    # genesis artifact must also satisfy the coupling
    with pytest.raises(ValueError, match="unreachable"):
        ArtifactEvolutionEngine(
            base_artifact=PlannerSpec(rework_hard_stop=10),
            bench=bench,
            rework_threshold=5,
        )
    # no threshold configured: knob uncoupled (free evolution mode)
    ArtifactEvolutionEngine(bench=bench).evaluate(PlannerSpec(version=2, rework_hard_stop=99))


def test_report_tracks_lineage():
    eng = _engine_with_hidden_dispute()
    candidate = PlannerSpec(version=2, rework_hard_stop=10)
    card = eng.evaluate(candidate)
    eng.accept(candidate, card)
    rep = eng.report()
    assert [r["version"] for r in rep] == [1, 2]
    assert rep[1]["parent"] == 1
    assert rep[1]["trust"] > rep[0]["trust"]
