"""FIDES Phase 1 — governed self-evolution: the Anchor tournament loop.

Propose AnchorSpec variants → replay deterministic task scenarios →
score → accept under governance (constitution + versioned anchor chain +
lineage archive). No LLM is in the evaluation or acceptance path.
"""

from .engine import (  # noqa: F401
    CandidateScorecard,
    ConstitutionError,
    EvolutionEngine,
    MockProposer,
    ScenarioResult,
)

__all__ = [
    "CandidateScorecard",
    "ConstitutionError",
    "EvolutionEngine",
    "MockProposer",
    "ScenarioResult",
]
