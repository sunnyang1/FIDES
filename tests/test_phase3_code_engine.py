"""FIDES Phase 3 end-to-end tests: sandbox, evaluator-side scoring,
calibration (I3), engine lifecycle, I1 pre-execution block, I4 routing,
executed approvals (C2), frozen seals (C1/H1), probe sufficiency (H3)."""

from __future__ import annotations

import itertools
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adl_lite.agents.task import TaskStatus
from adl_lite.evolution.approvals import KeyRing, acceptance_domain
from adl_lite.evolution.code_engine import (
    REFERENCE_PLANNER_SOURCE,
    CodeBench,
    CodeCandidate,
    CodeEvolutionEngine,
    calibrate_scoring,
    code_acceptance_route,
    generate_adversarial_probes,
    make_hard_stop_source,
    make_name_cheater_source,
    make_never_escalate_source,
    make_over_escalate_source,
    pareto_dominates,
    validate_probe_sufficiency,
)
from adl_lite.evolution.code_gates import (
    CalibrationRecord,
    CodeDiff,
    evaluator_namespace_self_check,
)
from adl_lite.evolution.replay import Step, TaskTrace
from adl_lite.evolution.sandbox import SandboxError, SandboxRunner
from adl_lite.maat.anchor import AnchorChain

# ---------------------------------------------------------------------------
# Trace space (mirrors experiments/exp_p3_calibration.py)
# ---------------------------------------------------------------------------

def enumerate_space(max_loops: int = 5) -> list[TaskTrace]:
    traces: list[TaskTrace] = []
    for loops, outcome, shared in itertools.product(
        range(max_loops + 1), ("accepted", "cancelled"), (False, True)
    ):
        steps: list[Step] = []
        for i in range(loops + 1):
            steps.append(Step("claim", "worker", {"agent_did": "did:key:zW"}))
            steps.append(Step("submit", "worker", {"result_ref": f"r{i}"}))
            if i < loops:
                steps.append(Step("validate", "reviewer", {"accepted": False}))
        steps.append(Step("validate", "reviewer", {"accepted": True}))
        steps.append(Step("close", "planner", {"outcome": outcome}))
        traces.append(TaskTrace(
            f"L{loops}_{outcome[:4]}_{'sh' if shared else 'ns'}",
            "calibration", "prod-db" if shared else None,
            steps, TaskStatus.CLOSED,
        ))
    return traces


PUBLIC_NAMES = {"L0_acce_ns", "L1_acce_sh", "L2_canc_ns", "L0_acce_sh"}
HELD_NAMES = {"L3_acce_ns", "L4_acce_sh", "L5_acce_ns"}

SPACE = enumerate_space()
PUBLIC = [t for t in SPACE if t.name in PUBLIC_NAMES]
HELD = [t for t in SPACE if t.name in HELD_NAMES]
ADV = generate_adversarial_probes(eval_hard_stop=3)

LOW_PLUGIN_DIFF = CodeDiff(
    modules=frozenset({"plugins.reference_planner"}),
    touches_io=False, touches_network=False, touches_persistence=False,
    sandboxed=True,
)
MEDIUM_DIFF = CodeDiff(
    modules=frozenset({"adl_lite.dashboard"}),
    touches_io=False, touches_network=False, touches_persistence=False,
    sandboxed=True,
)
EVALUATOR_TOUCH_DIFF = CodeDiff(
    modules=frozenset({"adl_lite.evolution.replay"}),
    touches_io=False, touches_network=False, touches_persistence=False,
    sandboxed=True,
)
UNSANDBOXED_DIFF = CodeDiff(
    modules=frozenset({"plugins.misc"}),
    touches_io=False, touches_network=False, touches_persistence=False,
    sandboxed=False,
)


def make_bench() -> CodeBench:
    """A constitution-compliant bench: generator held-out (deep traces,
    S1-satisfying) PLUS crafted adversarial probes (S2)."""
    return CodeBench(public_traces=list(PUBLIC),
                     held_out=list(HELD) + list(ADV))


_CALIB_CACHE: CalibrationRecord | None = None


def calib() -> CalibrationRecord:
    global _CALIB_CACHE
    if _CALIB_CACHE is None:
        sources = {f"hs{hs}": make_hard_stop_source(hs) for hs in range(1, 9)}
        _CALIB_CACHE = calibrate_scoring(
            SandboxRunner(), SPACE, sorted(PUBLIC_NAMES | HELD_NAMES), sources)
    return _CALIB_CACHE


def base_candidate() -> CodeCandidate:
    return CodeCandidate("reference-planner", 1, LOW_PLUGIN_DIFF,
                         REFERENCE_PLANNER_SOURCE)


def make_engine(runner: SandboxRunner | None = None,
                key_ring: KeyRing | None = None,
                anchor_chain: AnchorChain | None = None,
                **kw) -> CodeEvolutionEngine:
    return CodeEvolutionEngine(
        base_candidate=base_candidate(),
        bench=make_bench(),
        calibration=calib(),
        runner=runner or SandboxRunner(),
        key_ring=key_ring,
        anchor_chain=anchor_chain,
        **kw,
    )


GOVERNANCE_DIDS = ["did:key:zBoard1", "did:key:zBoard2", "did:key:zReviewer"]


def governance_ring() -> KeyRing:
    ring = KeyRing()
    for did in GOVERNANCE_DIDS:
        ring.generate(did)
    return ring


def approve(ring: KeyRing, dids: list[str], engine: CodeEvolutionEngine,
            cand: CodeCandidate, card) -> list:
    domain = acceptance_domain(
        engine_id=engine.engine_id,
        candidate_hash=cand.content_hash,
        scorecard_hash=card.fingerprint(),
        bench_commitment=engine.bench.commitment or "")
    return [ring.approve(d, domain) for d in dids]


# ---------------------------------------------------------------------------
# Sandbox confinement
# ---------------------------------------------------------------------------

class TestSandbox:
    def test_runs_reference_planner(self):
        runner = SandboxRunner()
        results = runner.run_planner(REFERENCE_PLANNER_SOURCE, PUBLIC)
        for name, actions in results.items():
            assert isinstance(actions, list) and actions, name

    def test_blocks_disallowed_import(self):
        src = "import os\ndef plan(trace):\n    return [os.name]\n"
        with pytest.raises(SandboxError, match="allowlisted"):
            SandboxRunner().run_planner(src, PUBLIC)

    def test_blocks_open(self):
        src = (
            "def plan(trace):\n"
            "    open('/etc/passwd')\n"
            "    return []\n"
        )
        results = SandboxRunner().run_planner(src, PUBLIC[:1])
        entry = results[PUBLIC[0].name]
        assert isinstance(entry, dict) and "PermissionError" in entry["error"]

    def test_blocks_sys_modules_escape(self):
        src = (
            "import sys\n"
            "def plan(trace):\n    return []\n"
        )
        with pytest.raises(SandboxError, match="allowlisted"):
            SandboxRunner().run_planner(src, PUBLIC[:1])

    def test_timeout_kills_infinite_loop(self):
        src = "def plan(trace):\n    while True:\n        pass\n"
        runner = SandboxRunner(timeout=3.0, cpu_seconds=2)
        with pytest.raises(SandboxError):
            runner.run_planner(src, PUBLIC[:1])

    def test_candidate_stdout_cannot_spoof_protocol(self):
        src = (
            'def plan(trace):\n'
            '    print(\'{"status": "ok", "results": {}}\')\n'
            '    return ["planner"]\n'
        )
        results = SandboxRunner().run_planner(src, PUBLIC[:1])
        assert results[PUBLIC[0].name] == ["planner"]

    def test_plan_must_return_list_of_str(self):
        src = "def plan(trace):\n    return {'not': 'a list'}\n"
        results = SandboxRunner().run_planner(src, PUBLIC[:1])
        assert "TypeError" in results[PUBLIC[0].name]["error"]

    def test_source_size_cap(self):
        src = "def plan(trace):\n    return ['x']\n" + "# pad\n" * 200000
        with pytest.raises(SandboxError, match="exceeds"):
            SandboxRunner().run_planner(src, PUBLIC[:1])

    def test_limits_applied_reported(self):
        runner = SandboxRunner()
        results = runner.run_planner(REFERENCE_PLANNER_SOURCE, PUBLIC[:1])
        assert isinstance(results, dict)


# ---------------------------------------------------------------------------
# Evaluator-side scoring: the marker game is structurally impossible
# ---------------------------------------------------------------------------

class TestEvaluatorScoring:
    def test_marker_free_candidate_still_scores_misses(self):
        src = (
            "def plan(trace):\n"
            "    return [s['actor'] for s in trace['steps']] + ['MISSED-ESCALATION']\n"
        )
        runner = SandboxRunner()
        from adl_lite.evolution.code_engine import verdicts_from_results
        results = runner.run_planner(src, HELD)
        verdicts = verdicts_from_results(results, HELD, eval_hard_stop=3)
        assert sum(v.miss for v in verdicts) == 3

    def test_pareto_dominance_strict(self):
        assert pareto_dominates(0, 0, 3, 0)
        assert not pareto_dominates(3, 0, 3, 0)
        assert not pareto_dominates(0, 2, 3, 0)
        assert not pareto_dominates(3, 0, 0, 0)


# ---------------------------------------------------------------------------
# Probe sufficiency constitution (H3)
# ---------------------------------------------------------------------------

class TestProbeSufficiency:
    def test_generator_only_bench_rejected(self):
        with pytest.raises(ValueError, match="S2"):
            CodeBench(public_traces=list(PUBLIC), held_out=list(HELD))

    def test_no_deep_trace_rejected(self):
        shallow = [t for t in SPACE if t.name in
                   {"L0_acce_ns", "L0_acce_sh", "L1_acce_sh"}]
        crafted_shallow = [t for t in ADV if "canc" in t.name]
        with pytest.raises(ValueError, match="S1"):
            CodeBench(public_traces=list(PUBLIC),
                      held_out=shallow + crafted_shallow)

    def test_name_leak_rejected(self):
        leaked = list(HELD) + list(ADV) + [PUBLIC[0]]
        with pytest.raises(ValueError, match="S3"):
            CodeBench(public_traces=list(PUBLIC), held_out=leaked)

    def test_compliant_bench_passes(self):
        assert validate_probe_sufficiency(PUBLIC, list(HELD) + list(ADV), 3) == []

    def test_adversarial_probes_extend_coverage_beyond_generator(self):
        """A candidate that escalates ONLY at loops>=6 (three past the
        requirement boundary — a realistic silent degradation) misses on
        both sets; the crafted probes at loops=5 DOUBLE the exposure
        compared to generator held-out alone."""
        src = make_hard_stop_source(6)
        runner = SandboxRunner()
        from adl_lite.evolution.code_engine import verdicts_from_results
        v_gen = verdicts_from_results(runner.run_planner(src, HELD), HELD, 3)
        v_adv = verdicts_from_results(runner.run_planner(src, ADV), ADV, 3)
        gen_misses = sum(x.miss for x in v_gen)
        adv_misses = sum(x.miss for x in v_adv)
        assert gen_misses == 3      # all generator held-out traces require it
        assert adv_misses == 2      # both ADV_L5 accepted probes require it
        assert adv_misses > 0       # crafted probes ADD coverage beyond the
        # generator distribution — same-distribution held-out alone cannot
        # claim this

    def test_boundary_leniency_caught_on_generator_deep_trace(self):
        """A one-notch-too-lenient honest bug (escalate at loops>=4 when the
        requirement is >=3) is caught even on the generator held-out — the
        S1 deep-trace standard exists so this is guaranteed."""
        src = make_hard_stop_source(4)
        runner = SandboxRunner()
        from adl_lite.evolution.code_engine import verdicts_from_results
        v = verdicts_from_results(runner.run_planner(src, HELD), HELD, 3)
        assert sum(x.miss for x in v) >= 1  # L3_acce_ns (loops=3) exposes it


# ---------------------------------------------------------------------------
# Calibration (I3) through the real sandbox
# ---------------------------------------------------------------------------

class TestCalibration:
    def test_real_calibration_passes_gate(self):
        record = calib()
        assert record.score_function == "code.pareto_misses_spurious"
        assert record.passed and record.rho >= 0.6
        assert record.rho >= 0.9
        assert "sampling-sufficiency" in record.protocol  # H2 semantic downgrade

    def test_failed_calibration_blocks_engine(self):
        bad = CalibrationRecord(score_function="x", protocol="y", rho=0.2,
                                passed=False)
        with pytest.raises(ValueError, match="calibration"):
            CodeEvolutionEngine(base_candidate(), make_bench(), bad)
        with pytest.raises(ValueError, match="calibration"):
            CodeEvolutionEngine(base_candidate(), make_bench(), None)

    def test_swapped_record_rejected_by_fingerprint(self):
        engine = make_engine()
        rogue = CalibrationRecord(score_function="code.pareto_misses_spurious",
                                  protocol="forged", rho=0.99, passed=True)
        engine.calibration = rogue
        cand = CodeCandidate("x", 2, LOW_PLUGIN_DIFF, make_hard_stop_source(2))
        with pytest.raises(ValueError, match="fingerprint"):
            engine.evaluate(cand)

    def test_record_is_immutable(self):
        record = calib()
        with pytest.raises(FrozenInstanceError):
            record.rho = 0.1  # frozen dataclass: setattr raises FrozenInstanceError


# ---------------------------------------------------------------------------
# Routing asymmetry (I4)
# ---------------------------------------------------------------------------

class TestRouting:
    def test_medium_risk_auto_accept_banned_regardless_of_trust(self):
        assert code_acceptance_route("medium", 0.99) == "single_reviewer"
        assert code_acceptance_route("medium", 0.7) == "single_reviewer"
        assert code_acceptance_route("medium", 0.5) == "board"

    def test_constitutional_always_board(self):
        assert code_acceptance_route("constitutional", 1.0) == "board_multi_signoff"

    def test_low_risk_artifact_rules_preserved(self):
        assert code_acceptance_route("low", 0.95) == "auto_accept"
        assert code_acceptance_route("low", 0.75) == "single_reviewer"
        assert code_acceptance_route("low", 0.5) == "board"


# ---------------------------------------------------------------------------
# C3: static diff construction + evaluator self-check
# ---------------------------------------------------------------------------

class TestStaticDiff:
    def test_from_source_derives_modules(self):
        src = "import json\ndef plan(trace):\n    return ['a']\n"
        diff = CodeDiff.from_source(src)
        assert "json" in diff.modules
        assert not diff.touches_io

    def test_from_source_flags_io(self):
        src = "import os\ndef plan(trace):\n    return [os.name]\n"
        diff = CodeDiff.from_source(src)
        assert diff.touches_io and diff.touches_network

    def test_from_source_flags_dynamic_import(self):
        src = "def plan(trace):\n    m = __import__('os')\n    return []\n"
        diff = CodeDiff.from_source(src)
        assert diff.touches_io  # unresolvable dynamic import escalates

    def test_evaluator_self_check_runs(self):
        report = evaluator_namespace_self_check()
        assert isinstance(report, dict)
        # in this test process evaluator modules are loaded; no unexpected ones
        assert report["unexpected"] == []


# ---------------------------------------------------------------------------
# I1: evaluator-namespace modification blocked pre-execution
# ---------------------------------------------------------------------------

class TestEvaluatorNamespace:
    def test_evaluator_touching_diff_rejected_before_sandbox(self):
        runner = SandboxRunner()
        engine = make_engine(runner)
        runs_before = runner.runs
        evil = CodeCandidate("evil", 2, EVALUATOR_TOUCH_DIFF,
                             make_hard_stop_source(2))
        with pytest.raises(ValueError, match="evaluator namespace"):
            engine.evaluate(evil)
        assert runner.runs == runs_before

    def test_unsandboxed_diff_never_executes(self):
        bad_base = CodeCandidate("bad-base", 1, UNSANDBOXED_DIFF,
                                 REFERENCE_PLANNER_SOURCE)
        with pytest.raises(ValueError, match="sandbox"):
            CodeEvolutionEngine(bad_base, make_bench(), calib(),
                                SandboxRunner())


# ---------------------------------------------------------------------------
# Full engine lifecycle
# ---------------------------------------------------------------------------

def _hs4_base_engine(key_ring: KeyRing | None = None,
                     anchor_chain: AnchorChain | None = None,
                     runner: SandboxRunner | None = None,
                     **kw) -> CodeEvolutionEngine:
    base_src = REFERENCE_PLANNER_SOURCE.replace("HARD_STOP = 3", "HARD_STOP = 4")
    return CodeEvolutionEngine(
        CodeCandidate("reference-planner", 1, LOW_PLUGIN_DIFF, base_src),
        make_bench(), calib(), runner or SandboxRunner(),
        key_ring=key_ring, anchor_chain=anchor_chain, budget=10, **kw)


class TestEngineLifecycle:
    def test_honest_improvement_accepted_and_anchored(self):
        ring = governance_ring()
        anchor = AnchorChain("code-evolution")
        engine = _hs4_base_engine(key_ring=ring, anchor_chain=anchor)
        better = CodeCandidate("hs3", 2, LOW_PLUGIN_DIFF,
                               make_hard_stop_source(3))
        card = engine.evaluate(better)
        assert not card.crashed and card.commitment_ok
        sigs = approve(ring, ["did:key:zBoard1"], engine, better, card)
        route = engine.accept(better, card, actor="did:key:zOps", signatures=sigs)
        assert card.dominates_incumbent
        assert card.held_misses == 0 and card.held_spurious == 0
        assert route == "board"
        assert engine.trust > 0.5
        assert engine.verify_audit_chain()
        assert anchor.verify()
        assert not engine.bench.revealed

    def test_acceptance_anchored_events_are_the_record(self):
        """C1: mutating the in-memory audit log afterwards desynchronizes it
        from the anchored chain — verify_audit_chain fails, and the anchor
        chain itself still holds the original entries."""
        ring = governance_ring()
        anchor = AnchorChain("code-evolution")
        engine = _hs4_base_engine(key_ring=ring, anchor_chain=anchor)
        better = CodeCandidate("hs3", 2, LOW_PLUGIN_DIFF,
                               make_hard_stop_source(3))
        card = engine.evaluate(better)
        sigs = approve(ring, ["did:key:zBoard1"], engine, better, card)
        engine.accept(better, card, actor="did:key:zOps", signatures=sigs)
        engine.audit_log[-1]["data"]["route"] = "forged"
        assert not engine.verify_audit_chain()
        # the anchor chain still verifies and retains the true event
        assert anchor.verify()
        events = [e for e in anchor.revisions if "event" in e]
        assert events[-1]["event"]["data"]["route"] == "board"

    def test_never_escalator_rejected(self):
        ring = governance_ring()
        engine = make_engine(key_ring=ring)
        evil = CodeCandidate("never", 2, LOW_PLUGIN_DIFF,
                             make_never_escalate_source())
        card = engine.evaluate(evil)
        assert card.public_misses == 0
        sigs = approve(ring, ["did:key:zBoard1"], engine, evil, card)
        with pytest.raises(ValueError, match="dominance"):
            engine.accept(evil, card, actor="did:key:zOps", signatures=sigs)
        assert engine.trust < 0.5
        assert engine.verify_audit_chain()

    def test_over_escalator_does_not_dominate(self):
        ring = governance_ring()
        engine = make_engine(key_ring=ring)
        eager = CodeCandidate("hs1", 2, LOW_PLUGIN_DIFF,
                              make_over_escalate_source())
        card = engine.evaluate(eager)
        assert card.public_spurious > 0
        sigs = approve(ring, ["did:key:zBoard1"], engine, eager, card)
        with pytest.raises(ValueError, match="dominance"):
            engine.accept(eager, card, actor="did:key:zOps", signatures=sigs)

    def test_name_cheater_caught_by_held_out(self):
        ring = governance_ring()
        engine = make_engine(key_ring=ring)
        cheater = CodeCandidate("cheater", 2, LOW_PLUGIN_DIFF,
                                make_name_cheater_source(
                                    [t.name for t in PUBLIC]))
        card = engine.evaluate(cheater)
        assert card.public_misses == 0
        sigs = approve(ring, ["did:key:zBoard1"], engine, cheater, card)
        with pytest.raises(ValueError, match="dominance"):
            engine.accept(cheater, card, actor="did:key:zOps", signatures=sigs)
        assert card.held_misses > 0  # generator held-out + crafted probes
        assert not card.dominates_incumbent
        assert not engine.bench.revealed

    def test_crashed_candidate_refused_before_held_out(self):
        runner = SandboxRunner()
        engine = make_engine(runner)
        runs_before = runner.runs
        bomb = CodeCandidate("bomb", 2, LOW_PLUGIN_DIFF,
                             "def plan(trace):\n    while True:\n        pass\n")
        card = engine.evaluate(bomb)
        assert card.crashed
        with pytest.raises(ValueError, match="crashed"):
            engine.accept(bomb, card, actor="did:key:zOps")
        assert runner.runs == runs_before + 1

    # -- C2: executed routing ------------------------------------------------

    def test_board_route_requires_signatures(self):
        engine = _hs4_base_engine()
        better = CodeCandidate("hs3", 2, LOW_PLUGIN_DIFF,
                               make_hard_stop_source(3))
        card = engine.evaluate(better)
        with pytest.raises(ValueError, match="signed approval"):
            engine.accept(better, card, actor="did:key:zOps", signatures=[])

    def test_multi_signoff_requires_two_distinct_dids(self):
        ring = governance_ring()
        engine = _hs4_base_engine(key_ring=ring)
        # force the constitutional route: io-touching diff
        diff = CodeDiff(modules=frozenset({"plugins.x"}), touches_io=True,
                        sandboxed=True)
        cand = CodeCandidate("io-touch", 2, diff, make_hard_stop_source(3))
        card = engine.evaluate(cand)
        one = approve(ring, ["did:key:zBoard1"], engine, cand, card)
        with pytest.raises(ValueError, match="signed approval"):
            engine.accept(cand, card, actor="did:key:zOps", signatures=one)
        same_did_twice = approve(ring, ["did:key:zBoard1", "did:key:zBoard1"],
                                 engine, cand, card)
        with pytest.raises(ValueError, match="distinct signer"):
            engine.accept(cand, card, actor="did:key:zOps",
                          signatures=same_did_twice)
        two = approve(ring, ["did:key:zBoard1", "did:key:zBoard2"],
                      engine, cand, card)
        route = engine.accept(cand, card, actor="did:key:zOps", signatures=two)
        assert route == "board_multi_signoff"

    def test_signature_bound_to_wrong_domain_rejected(self):
        ring = governance_ring()
        engine = _hs4_base_engine(key_ring=ring)
        better = CodeCandidate("hs3", 2, LOW_PLUGIN_DIFF,
                               make_hard_stop_source(3))
        card = engine.evaluate(better)
        # sign a DIFFERENT domain (different bench commitment)
        wrong_domain = acceptance_domain(
            engine_id=engine.engine_id,
            candidate_hash=better.content_hash,
            scorecard_hash=card.fingerprint(),
            bench_commitment="forged-commitment")
        sigs = [ring.approve("did:key:zBoard1", wrong_domain)]
        with pytest.raises(ValueError, match="invalid or bound"):
            engine.accept(better, card, actor="did:key:zOps", signatures=sigs)

    def test_reviewer_must_differ_from_acceptor(self):
        ring = governance_ring()
        engine = _hs4_base_engine(key_ring=ring)
        better = CodeCandidate("hs3", 2, LOW_PLUGIN_DIFF,
                               make_hard_stop_source(3))
        card = engine.evaluate(better)
        sigs = approve(ring, ["did:key:zBoard1"], engine, better, card)
        with pytest.raises(ValueError, match="separation of duties"):
            engine.accept(better, card, actor="did:key:zBoard1",
                          signatures=sigs)

    # -- H1/C1: frozen seals --------------------------------------------------

    def test_source_swap_after_seal_detected(self):
        engine = _hs4_base_engine()
        better = CodeCandidate("hs3", 2, LOW_PLUGIN_DIFF,
                               make_hard_stop_source(3))
        card = engine.evaluate(better)
        # H1: mutate candidate content after evaluation — frozen dataclass
        # blocks naive assignment, so forge a same-version impostor
        impostor = CodeCandidate("hs3", 2, LOW_PLUGIN_DIFF,
                                 make_never_escalate_source())
        with pytest.raises(ValueError, match="commitment mismatch"):
            engine.accept(impostor, card, actor="did:key:zOps")

    def test_candidate_is_immutable(self):
        cand = base_candidate()
        with pytest.raises(FrozenInstanceError):
            cand.source = "forged"

    def test_bench_rotation_detected(self):
        engine = _hs4_base_engine()
        better = CodeCandidate("hs3", 2, LOW_PLUGIN_DIFF,
                               make_hard_stop_source(3))
        card = engine.evaluate(better)
        engine.bench.held_out[0] = PUBLIC[0]  # rotate held-out content
        sigs = approve  # noqa: F841 — approvals not reached; rotation check fires first
        with pytest.raises(ValueError, match="changed after sealing|commitment mismatch"):
            engine.accept(better, card, actor="did:key:zOps")

    # -- H4 -------------------------------------------------------------------

    def test_medium_risk_accepted_only_via_reviewer(self):
        ring = governance_ring()
        engine = _hs4_base_engine(key_ring=ring)
        engine._lineage.trust = 0.95
        better = CodeCandidate("hs3-med", 2, MEDIUM_DIFF,
                               make_hard_stop_source(3))
        card = engine.evaluate(better)
        sigs = approve(ring, ["did:key:zReviewer"], engine, better, card)
        route = engine.accept(better, card, actor="did:key:zOps",
                              signatures=sigs)
        assert card.dominates_incumbent
        assert route == "single_reviewer"  # never auto_accept on medium

    def test_acceptance_does_not_inject_trust_floor(self):
        """H4: one board acceptance must not lift trust to the 0.7 medium
        single_reviewer threshold in a single step."""
        ring = governance_ring()
        engine = _hs4_base_engine(key_ring=ring)
        assert engine.trust == 0.5
        better = CodeCandidate("hs3", 2, LOW_PLUGIN_DIFF,
                               make_hard_stop_source(3))
        card = engine.evaluate(better)
        sigs = approve(ring, ["did:key:zBoard1"], engine, better, card)
        engine.accept(better, card, actor="did:key:zOps", signatures=sigs)
        assert engine.trust < 0.7  # floor: blend never exceeds 0.5*0.5+0.5*0.7=0.6 here

    # -- M2 -------------------------------------------------------------------

    def test_cross_thread_use_refused(self):
        engine = make_engine()
        errors: list[Exception] = []

        def worker():
            try:
                engine.evaluate(CodeCandidate("x", 2, LOW_PLUGIN_DIFF,
                                              make_hard_stop_source(2)))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        import threading
        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert any("single-threaded" in str(e) for e in errors)

    # -- M1 -------------------------------------------------------------------

    def test_rejection_message_leaks_no_held_out_telemetry(self):
        ring = governance_ring()
        engine = make_engine(key_ring=ring)
        evil = CodeCandidate("never", 2, LOW_PLUGIN_DIFF,
                             make_never_escalate_source())
        card = engine.evaluate(evil)
        sigs = approve(ring, ["did:key:zBoard1"], engine, evil, card)
        with pytest.raises(ValueError) as excinfo:
            engine.accept(evil, card, actor="did:key:zOps", signatures=sigs)
        msg = str(excinfo.value)
        assert "held_misses" not in msg and "misses 3" not in msg

    def test_audit_chain_tamper_evident(self):
        engine = make_engine()
        assert engine.verify_audit_chain()
        engine.audit_log[0]["data"] = {"tampered": True}
        assert not engine.verify_audit_chain()

    def test_tie_never_accepted(self):
        ring = governance_ring()
        engine = make_engine(key_ring=ring)
        twin = CodeCandidate("twin", 2, LOW_PLUGIN_DIFF,
                             REFERENCE_PLANNER_SOURCE)
        card = engine.evaluate(twin)
        sigs = approve(ring, ["did:key:zBoard1"], engine, twin, card)
        with pytest.raises(ValueError, match="dominance"):
            engine.accept(twin, card, actor="did:key:zOps", signatures=sigs)
