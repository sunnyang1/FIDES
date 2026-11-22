"""FIDES Phase 1 — the governed evolution engine (Anchor tournament).

The loop:

    propose(candidate anchor)                # mutation (LLM or seeded search)
        → constitution check                 # immutable invariants, deterministic
        → replay scenarios                   # deterministic, no LLM in evaluation
        → scorecard vs incumbent             # safety metrics must not regress
        → human accept (governed close)      # anchor revision anchored on-chain
        → lineage archive grows by one fork  # rollback = fork back, never delete

Design invariants (see docs/governed-evolution.md):
  * The evaluator is constitutionally outside the mutation space. A candidate
    that touches immutable fields is rejected BEFORE any scenario runs —
    this is the FIDES answer to the DGM "agent deleted the detector's log
    markers" failure mode.
  * Everything the acceptance decision relies on is derived from EventChains
    (counts, integrity, gate audit trail) — replayable after the fact.
  * "Rollback" is a fork in the lineage, not a deletion: CRDT LUB semantics
    never regress, so reverting = forking from an older ancestor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..agents.identity import AgentProfile, AgentRegistry, AgentRole
from ..agents.task import TaskStatus
from ..consensus import ConsensusEngine
from ..exceptions import GateViolation
from ..maat import AnchorSpec, GovernedTaskRegistry
from ..maat.anchor import AnchorChain
from ..maat.gates import count_rework_loops


class ConstitutionError(Exception):
    """A candidate anchor violates the immutable constitution."""


class Proposer(Protocol):
    def propose(self, base: AnchorSpec, lineage: list[AnchorSpec]) -> AnchorSpec: ...


# ---------------------------------------------------------------------------
# Constitution — the immutable layer of the mutation space
# ---------------------------------------------------------------------------

# Payload fields whose removal would blind the governance layer. The DGM
# incident (agent removed the markers the detector used to flag hallucinations)
# is the motivating example: the detector's input channel is constitutionally
# read-only for candidates.
IMMUTABLE_REQUIRED_FIELDS: dict[str, list[str]] = {
    "task_validate": ["accepted"],
    "task_submit": ["result_ref"],
}

# Gates that may never all be disabled at once; identity and lineage stay
# mandatory even when the operator relaxes the others.
ALWAYS_ON_GATES = ("identity", "lineage")


def check_constitution(candidate: AnchorSpec) -> None:
    """Deterministic; runs before any scenario. Raises ConstitutionError."""
    for et, fields in IMMUTABLE_REQUIRED_FIELDS.items():
        have = candidate.required_payload_fields.get(et, [])
        removed = [f for f in fields if f not in have]
        if removed:
            raise ConstitutionError(
                f"candidate removes immutable payload fields for {et}: {removed}"
            )
    if candidate.rework_threshold < 1:
        raise ConstitutionError("rework_threshold must be >= 1")
    if all(
        not getattr(candidate, f"enforce_{g}")
        for g in ("identity", "lineage", "circuit_breaker")
    ):
        raise ConstitutionError("at least one safety gate family must remain enforced")
    if not candidate.enforce_identity or not candidate.enforce_lineage:
        raise ConstitutionError(
            f"gates {ALWAYS_ON_GATES} are constitutionally always-on"
        )
    if not isinstance(candidate.shared_resources, list):
        raise ConstitutionError("shared_resources must be a list")


# ---------------------------------------------------------------------------
# Deterministic scenarios — the evaluation bench
# ---------------------------------------------------------------------------

ROOT_DID = "did:key:zRoot"


def _seed_agents(engine: ConsensusEngine, *names: str) -> dict[str, str]:
    reg = AgentRegistry(engine, admin_public_keys={ROOT_DID: "key"}, admin_calls_allowed=True)
    dids = {}
    for name in names:
        did = f"did:key:z{name}"
        reg.register_agent(AgentProfile(did=did, role=AgentRole.DISCOVERER, name=name))
        reg.validate_agent(did, ROOT_DID, reason="bootstrap", admin=True)
        dids[name] = did
    return dids


def scenario_normal(engine: ConsensusEngine, anchor: AnchorSpec) -> ScenarioResult:
    """Green path: full lifecycle closes cleanly."""
    dids = _seed_agents(engine, "Alice", "Val")
    reg = GovernedTaskRegistry(engine, anchor=anchor, agents=AgentRegistry(engine))
    t = reg.create_task("normal task", required_capabilities=["related-to"])
    reg.claim(t.task_id, dids["Alice"])
    reg.submit(t.task_id, dids["Alice"], "r1")
    reg.validate_result(t.task_id, dids["Val"], accepted=True)
    reg.close(t.task_id, "planner", "accepted")
    chain = engine.chains[t.task_id]
    return ScenarioResult(
        name="normal",
        completed=reg.get_task(t.task_id).status == TaskStatus.CLOSED,
        integrity=chain.verify_integrity(),
        steps=len(chain.events),
        violations=0,
        rework_loops=0,
    )


def scenario_pingpong(engine: ConsensusEngine, anchor: AnchorSpec) -> ScenarioResult:
    """Adversarial: two agents bounce a task forever. A good anchor halts it."""
    dids = _seed_agents(engine, "A1", "V1")
    reg = GovernedTaskRegistry(engine, anchor=anchor, agents=AgentRegistry(engine))
    t = reg.create_task("flaky", db_ref="prod-db")
    violations = 0
    for _ in range(anchor.rework_threshold + 5):
        try:
            reg.claim(t.task_id, dids["A1"])
            reg.submit(t.task_id, dids["A1"], "r")
            reg.validate_result(t.task_id, dids["V1"], accepted=False)
        except GateViolation:
            violations += 1
            break
    chain = engine.chains[t.task_id]
    loops = count_rework_loops(chain.events)
    halted = violations > 0 and loops < anchor.rework_threshold + 5
    return ScenarioResult(
        name="pingpong",
        completed=False,
        integrity=chain.verify_integrity(),
        steps=len(chain.events),
        violations=violations,
        rework_loops=loops,
        halted=halted,
    )


def scenario_orphan(engine: ConsensusEngine, anchor: AnchorSpec) -> ScenarioResult:
    """Poison 2: forged parent must never mint a live task."""
    reg = GovernedTaskRegistry(engine, anchor=anchor, agents=AgentRegistry(engine))
    blocked = 0
    try:
        reg.create_task("evil child", parent_task_id="task:nonexistent")
    except GateViolation:
        blocked += 1
    minted = any(
        c.events[0].payload.get("objective") == "evil child" for c in engine.chains.values()
    )
    return ScenarioResult(
        name="orphan",
        completed=False,
        integrity=True,
        steps=0,
        violations=blocked,
        rework_loops=0,
        halted=(blocked > 0 and not minted),
    )


def scenario_contention(engine: ConsensusEngine, anchor: AnchorSpec) -> ScenarioResult:
    """Shared resource: second claim on a held shared object must be refused."""
    dids = _seed_agents(engine, "A1", "A2")
    reg = GovernedTaskRegistry(engine, anchor=anchor, agents=AgentRegistry(engine))
    t1 = reg.create_task("migration", db_ref="prod-db")
    t2 = reg.create_task("hotfix", db_ref="prod-db")
    reg.claim(t1.task_id, dids["A1"])
    blocked = 0
    try:
        reg.claim(t2.task_id, dids["A2"])
    except GateViolation:
        blocked += 1
    return ScenarioResult(
        name="contention",
        completed=False,
        integrity=True,
        steps=0,
        violations=blocked,
        rework_loops=0,
        halted=(blocked > 0),
    )


DEFAULT_SCENARIOS: list[Callable[[ConsensusEngine, AnchorSpec], ScenarioResult]] = [
    scenario_normal,
    scenario_pingpong,
    scenario_orphan,
    scenario_contention,
]


@dataclass
class ScenarioResult:
    name: str
    completed: bool
    integrity: bool
    steps: int
    violations: int
    rework_loops: int
    halted: bool = False


@dataclass
class CandidateScorecard:
    candidate_version: int
    results: list[ScenarioResult]
    constitution_ok: bool = True
    accepted: bool = False

    @property
    def all_integrity(self) -> bool:
        return all(r.integrity for r in self.results)

    @property
    def safety_violations_caught(self) -> int:
        """Adversarial scenarios that were deterministically halted."""
        return sum(1 for r in self.results if r.name != "normal" and r.halted)

    @property
    def normal_completed(self) -> bool:
        return all(r.completed for r in self.results if r.name == "normal")

    def dominates(self, other: CandidateScorecard) -> bool:
        """Safety never regresses, capability never regresses."""
        return (
            self.normal_completed >= other.normal_completed
            and self.safety_violations_caught >= other.safety_violations_caught
            and self.all_integrity
            and (
                self.safety_violations_caught > other.safety_violations_caught
                or self.normal_completed > other.normal_completed
            )
        )


# ---------------------------------------------------------------------------
# Proposers
# ---------------------------------------------------------------------------


class MockProposer:
    """Seeded neighborhood search — deterministic stand-in for an LLM proposer."""

    def __init__(self, seed: int = 0) -> None:
        self._n = seed

    def propose(self, base: AnchorSpec, lineage: list[AnchorSpec]) -> AnchorSpec:
        self._n += 1
        variant = base.model_copy(deep=True)
        variant.version = base.version + 1
        knobs = ["rework_threshold", "shared_resources"]
        knob = knobs[self._n % len(knobs)]
        if knob == "rework_threshold":
            variant.rework_threshold = max(1, base.rework_threshold + (-1) ** self._n)
        else:
            pool = ["prod-db", "wallet-svc", "ci-runner"]
            cur = set(variant.shared_resources)
            nxt = pool[self._n % len(pool)]
            variant.shared_resources = (
                sorted(cur - {nxt}) if nxt in cur else sorted(cur | {nxt})
            )
        return variant


class LLMProposer:
    """Hook for an LLM mutation operator. The LLM proposes; it never evaluates
    and never accepts — the evaluation and acceptance path stays deterministic.
    """

    def __init__(self, mutate: Callable[[AnchorSpec, list[dict]], dict]) -> None:
        self._mutate = mutate

    def propose(self, base: AnchorSpec, lineage: list[AnchorSpec]) -> AnchorSpec:
        raw = self._mutate(base, [a.model_dump() for a in lineage])
        return AnchorSpec(**{**base.model_dump(), **raw, "version": base.version + 1})


# ---------------------------------------------------------------------------
# Evolution engine
# ---------------------------------------------------------------------------


@dataclass
class LineageEntry:
    """One generation: the accepted anchor + its scorecard."""

    version: int
    spec: AnchorSpec
    scorecard: CandidateScorecard
    parent_version: int | None


@dataclass
class EvolutionEngine:
    """Runs the propose → evaluate → accept loop under governance.

    The engine itself is deliberately small: all state that matters is in
    AnchorChain (versioned contract history) and the lineage list (which is
    derivable from the anchor chain's audit trail).
    """

    base_anchor: AnchorSpec = field(default_factory=AnchorSpec)
    scenarios: list = field(default_factory=lambda: list(DEFAULT_SCENARIOS))
    proposer: Proposer = field(default_factory=MockProposer)
    budget: int = 20  # max generations before forced escalation

    def __post_init__(self) -> None:
        check_constitution(self.base_anchor)
        self.anchor_chain = AnchorChain(self.base_anchor.anchor_id)
        self.anchor_chain.append(self.base_anchor, actor="genesis")
        incumbent_card = self._evaluate(self.base_anchor)
        incumbent_card.accepted = True
        self.lineage: list[LineageEntry] = [
            LineageEntry(self.base_anchor.version, self.base_anchor, incumbent_card, None)
        ]
        self._generations = 0

    # -- evaluation -----------------------------------------------------

    def _evaluate(self, anchor: AnchorSpec) -> CandidateScorecard:
        results = []
        for scenario in self.scenarios:
            engine = ConsensusEngine(dev_mode=True)
            results.append(scenario(engine, anchor))
        return CandidateScorecard(anchor.version, results)

    # -- the loop ---------------------------------------------------------

    def propose_candidate(self) -> AnchorSpec:
        if self._generations >= self.budget:
            raise ConstitutionError(
                f"evolution budget exhausted ({self.budget} generations): escalate to human"
            )
        return self.proposer.propose(self.lineage[-1].spec, [e.spec for e in self.lineage])

    def evaluate_candidate(self, candidate: AnchorSpec) -> CandidateScorecard:
        """Constitution first, scenarios second — mirroring the gate order."""
        check_constitution(candidate)
        card = self._evaluate(candidate)
        incumbent = self.lineage[-1].scorecard
        card.dominates_incumbent = card.dominates(incumbent)  # type: ignore[attr-defined]
        return card

    def accept(self, candidate: AnchorSpec, card: CandidateScorecard, actor: str) -> None:
        """Human-signed acceptance (the 'governed close' of the meta-task).

        Raises unless the scorecard strictly improves on the incumbent —
        acceptance is a deterministic function of recorded facts, not a vibe.
        """
        if not card.dominates_incumbent:  # type: ignore[attr-defined]
            raise ConstitutionError(
                "candidate does not dominate incumbent on safety+capability; "
                "rejected by acceptance gate"
            )
        self.anchor_chain.append(candidate, actor=actor)
        card.accepted = True
        self.lineage.append(
            LineageEntry(candidate.version, candidate, card, self.lineage[-1].version)
        )
        self._generations += 1

    def rollback(self, to_version: int, actor: str) -> AnchorSpec:
        """Revert = fork back to an ancestor and anchor the fork as a new
        revision. Nothing is deleted; the lineage records the detour."""
        target = next((e for e in self.lineage if e.version == to_version), None)
        if target is None:
            raise ValueError(f"version {to_version} not in lineage")
        fork = target.spec.model_copy(update={"version": self.lineage[-1].spec.version + 1})
        self.anchor_chain.append(fork, actor=f"{actor}:rollback-to-v{to_version}")
        card = self._evaluate(fork)
        card.accepted = True
        self.lineage.append(LineageEntry(fork.version, fork, card, to_version))
        return fork

    def report(self) -> list[dict[str, Any]]:
        return [
            {
                "version": e.version,
                "parent": e.parent_version,
                "rework_threshold": e.spec.rework_threshold,
                "shared_resources": e.spec.shared_resources,
                "safety_halted": e.scorecard.safety_violations_caught,
                "normal_completed": e.scorecard.normal_completed,
                "accepted": e.scorecard.accepted,
            }
            for e in self.lineage
        ]
