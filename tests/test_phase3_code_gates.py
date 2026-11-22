"""FIDES Phase 3 tests — code-level evolution constitution gates.

The P4 drill caveat named the boundary: attacking the score function
itself is out of scope for Phase 2. Phase 3 closes it structurally:
the evaluator namespace is permanently outside the mutation space.
"""

from __future__ import annotations

import pytest

from adl_lite.evolution.code_gates import (
    CalibrationRecord,
    CodeDiff,
    Phase3Gate,
    check_calibration,
    check_evaluator_namespace,
    check_sandbox,
    diff_escalation,
)


def _ns_hit_diff(**kw) -> CodeDiff:
    return CodeDiff(modules=frozenset({"adl_lite.evolution.replay"}), **kw)


# ---------------------------------------------------------------------------
# Evaluator namespace — the permanent boundary
# ---------------------------------------------------------------------------


def test_evaluator_namespace_touches_are_constitutional():
    diff = _ns_hit_diff(sandboxed=True)
    assert diff_escalation(diff) == "constitutional"


def test_evaluator_namespace_raises_before_evaluation():
    diff = _ns_hit_diff(sandboxed=True)
    with pytest.raises(ValueError, match="evaluator namespace"):
        check_evaluator_namespace(diff)
    with pytest.raises(ValueError, match="evaluator namespace"):
        Phase3Gate().check(diff, calibration=None)


def test_namespace_protection_is_not_waived_by_sandbox():
    """Even a perfectly sandboxed candidate cannot touch the evaluator.
    Sandboxing confines execution; the namespace check confines the DIFF."""
    diff = _ns_hit_diff(sandboxed=True, touches_io=False)
    with pytest.raises(ValueError, match="evaluator namespace"):
        check_evaluator_namespace(diff)


def test_custom_namespace_respected():
    custom_ns = frozenset({"my.evaluator", "my.scores"})
    diff = CodeDiff(modules=frozenset({"my.evaluator"}), sandboxed=True)
    with pytest.raises(ValueError, match="evaluator namespace"):
        check_evaluator_namespace(diff, evaluator_ns=custom_ns)
    ok = CodeDiff(modules=frozenset({"adl_lite.evolution.replay"}), sandboxed=True)
    # replay is in the DEFAULT namespace but not in the custom one
    check_evaluator_namespace(ok, evaluator_ns=custom_ns)


# ---------------------------------------------------------------------------
# Capability tiers
# ---------------------------------------------------------------------------


def test_io_network_persistence_escalate_to_constitutional():
    for kw in ({"touches_io": True}, {"touches_network": True},
               {"touches_persistence": True}):
        diff = CodeDiff(modules=frozenset({"app.plugin"}), sandboxed=True, **kw)
        assert diff_escalation(diff) == "constitutional"


def test_unsandboxed_is_constitutional():
    diff = CodeDiff(modules=frozenset({"app.plugin"}), sandboxed=False)
    assert diff_escalation(diff) == "constitutional"
    with pytest.raises(ValueError, match="not sandboxed"):
        check_sandbox(diff)


def test_core_package_medium_risk():
    diff = CodeDiff(modules=frozenset({"adl_lite.dashboard"}), sandboxed=True)
    assert diff_escalation(diff) == "medium"


def test_external_plugin_low_risk():
    diff = CodeDiff(modules=frozenset({"plugins.scenario_runner"}), sandboxed=True)
    assert diff_escalation(diff) == "low"


# ---------------------------------------------------------------------------
# Calibration precondition
# ---------------------------------------------------------------------------


def test_calibration_record_required():
    with pytest.raises(ValueError, match="no calibration record"):
        check_calibration(None)


def test_calibration_must_pass_gate():
    failing = CalibrationRecord("code_score", "exp_p3_calibration",
                                rho=0.4, passed=True)
    with pytest.raises(ValueError, match="failed calibration"):
        check_calibration(failing)
    passing = CalibrationRecord("code_score", "exp_p3_calibration",
                                rho=0.75, passed=True)
    check_calibration(passing)  # no raise


def test_phase3_gate_composition_order():
    """A diff that both touches the evaluator AND lacks calibration fails
    on the namespace check first — the evaluator is protected even if the
    calibration plumbing is buggy."""
    diff = _ns_hit_diff(sandboxed=True)
    with pytest.raises(ValueError, match="evaluator namespace"):
        Phase3Gate().check(diff, calibration=None)


def test_phase3_gate_full_pass():
    diff = CodeDiff(modules=frozenset({"plugins.new_scorer"}), sandboxed=True)
    cal = CalibrationRecord("new_scorer", "exp_p3_calibration", rho=0.8, passed=True)
    route = Phase3Gate().check(diff, calibration=cal)
    assert route == "low"
