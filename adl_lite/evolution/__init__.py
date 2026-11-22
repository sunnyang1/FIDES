"""FIDES governed self-evolution.

Phase 1 — Anchor tournament: propose AnchorSpec variants → replay
deterministic task scenarios → score → accept under governance
(constitution + versioned anchor chain + lineage archive).

Phase 2 — Artifact evolution: bring L1 artifacts (PlannerSpec) into the
same loop, with a commit–reveal held-out bench, diff-risk × reputation
acceptance routing, and per-lineage budgets. No LLM is in the evaluation
or acceptance path.

Phase 3 — Code-level self-modification: candidates are *source code*, run
inside a confined sandbox subprocess; constitution gates (evaluator
namespace, sandbox, calibration) fire before any execution; scoring is
evaluator-side; acceptance is strict Pareto dominance routed by risk ×
trust, with auto_accept banned on medium risk; every event lands in a
hash-chained anchored audit log.
"""

from .approvals import (
    APPROVAL_POLICY,
    KeyRing,
    SignedApproval,
    acceptance_domain,
)
from .code_engine import (
    REFERENCE_PLANNER_SOURCE,
    CodeBench,
    CodeCandidate,
    CodeEvolutionEngine,
    CodeLineage,
    CodeScorecard,
    calibrate_against_history,
    calibrate_scoring,
    candidate_source_hash,
    code_acceptance_route,
    count_loops_trace,
    dominance_with_latency,
    escalation_required,
    generate_adversarial_probes,
    make_hard_stop_source,
    make_name_cheater_source,
    make_never_escalate_source,
    make_over_escalate_source,
    pareto_dominates,
    spearman_tie_aware,
    validate_probe_sufficiency,
    verdicts_from_results,
)
from .code_gates import (
    DEFAULT_EVALUATOR_NAMESPACE,
    CalibrationRecord,
    CodeDiff,
    Phase3Gate,
    check_calibration,
    check_evaluator_namespace,
    evaluator_namespace_self_check,
)
from .engine import (
    CandidateScorecard,
    ConstitutionError,
    EvolutionEngine,
    MockProposer,
    ScenarioResult,
)
from .replay import (
    ArtifactEvolutionEngine,
    ArtifactLineage,
    ArtifactScorecard,
    HeldOutBench,
    PlannerSpec,
    SeededArtifactProposer,
    TaskTrace,
    acceptance_route,
    diff_risk,
    sample_traces,
    score_plans,
)
from .sandbox import (
    SandboxError,
    SandboxRunner,
    SandboxTimeout,
)

__all__ = [
    "APPROVAL_POLICY",
    "DEFAULT_EVALUATOR_NAMESPACE",
    "REFERENCE_PLANNER_SOURCE",
    "ArtifactEvolutionEngine",
    "ArtifactLineage",
    "ArtifactScorecard",
    "CalibrationRecord",
    "CandidateScorecard",
    "CodeBench",
    "CodeCandidate",
    "CodeDiff",
    "CodeEvolutionEngine",
    "CodeLineage",
    "CodeScorecard",
    "ConstitutionError",
    "EvolutionEngine",
    "HeldOutBench",
    "KeyRing",
    "MockProposer",
    "Phase3Gate",
    "PlannerSpec",
    "SandboxError",
    "SandboxRunner",
    "SandboxTimeout",
    "ScenarioResult",
    "SeededArtifactProposer",
    "SignedApproval",
    "TaskTrace",
    "acceptance_domain",
    "acceptance_route",
    "calibrate_against_history",
    "calibrate_scoring",
    "candidate_source_hash",
    "check_calibration",
    "check_evaluator_namespace",
    "code_acceptance_route",
    "count_loops_trace",
    "diff_risk",
    "dominance_with_latency",
    "escalation_required",
    "evaluator_namespace_self_check",
    "generate_adversarial_probes",
    "make_hard_stop_source",
    "make_name_cheater_source",
    "make_never_escalate_source",
    "make_over_escalate_source",
    "pareto_dominates",
    "sample_traces",
    "score_plans",
    "spearman_tie_aware",
    "validate_probe_sufficiency",
    "verdicts_from_results",
]
