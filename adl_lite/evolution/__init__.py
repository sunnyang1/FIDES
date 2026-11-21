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

from .engine import (  # noqa: F401
    CandidateScorecard,
    ConstitutionError,
    EvolutionEngine,
    MockProposer,
    ScenarioResult,
)
from .replay import (  # noqa: F401
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
from .code_gates import (  # noqa: F401
    DEFAULT_EVALUATOR_NAMESPACE,
    CalibrationRecord,
    CodeDiff,
    Phase3Gate,
)
from .sandbox import (  # noqa: F401
    SandboxError,
    SandboxRunner,
    SandboxTimeout,
)
from .code_engine import (  # noqa: F401
    REFERENCE_PLANNER_SOURCE,
    CodeBench,
    CodeCandidate,
    CodeEvolutionEngine,
    CodeLineage,
    CodeScorecard,
    calibrate_scoring,
    code_acceptance_route,
    escalation_required,
    make_hard_stop_source,
    make_name_cheater_source,
    make_never_escalate_source,
    make_over_escalate_source,
    pareto_dominates,
    spearman_tie_aware,
    verdicts_from_results,
)

__all__ = [
    "CandidateScorecard",
    "ConstitutionError",
    "EvolutionEngine",
    "MockProposer",
    "ScenarioResult",
    "ArtifactEvolutionEngine",
    "ArtifactLineage",
    "ArtifactScorecard",
    "HeldOutBench",
    "PlannerSpec",
    "SeededArtifactProposer",
    "TaskTrace",
    "acceptance_route",
    "diff_risk",
    "sample_traces",
    "score_plans",
    "DEFAULT_EVALUATOR_NAMESPACE",
    "CalibrationRecord",
    "CodeDiff",
    "Phase3Gate",
    "SandboxError",
    "SandboxRunner",
    "SandboxTimeout",
    "REFERENCE_PLANNER_SOURCE",
    "CodeBench",
    "CodeCandidate",
    "CodeEvolutionEngine",
    "CodeLineage",
    "CodeScorecard",
    "calibrate_scoring",
    "code_acceptance_route",
    "escalation_required",
    "make_hard_stop_source",
    "make_name_cheater_source",
    "make_never_escalate_source",
    "make_over_escalate_source",
    "pareto_dominates",
    "spearman_tie_aware",
    "verdicts_from_results",
]
