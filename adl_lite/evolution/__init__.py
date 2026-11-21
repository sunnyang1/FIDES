"""FIDES governed self-evolution.

Phase 1 — Anchor tournament: propose AnchorSpec variants → replay
deterministic task scenarios → score → accept under governance
(constitution + versioned anchor chain + lineage archive).

Phase 2 — Artifact evolution: bring L1 artifacts (PlannerSpec) into the
same loop, with a commit–reveal held-out bench, diff-risk × reputation
acceptance routing, and per-lineage budgets.

Phase 3 — Code-level gates: evaluator namespace permanently outside the
mutation space, sandboxed execution precondition, P3-calibrated scoring
precondition, code diff risk-tiering.

No LLM is in the evaluation or acceptance path.
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
]
