"""FIDES Phase 2 — artifact evolution: replay scenario sets + held-out bench.

Extends the Phase-1 Anchor tournament from "mutate the governance contract"
to "mutate L1 artifacts" (planner configs, prompt templates). The evaluation
path stays deterministic: a scenario set is a list of recorded task traces
(TaskTrace), and scoring replays each trace under a candidate artifact,
counting where the gate layer would have behaved differently.

Anti-overfitting: the public scenario set is visible to the proposer; the
held-out set is commit–revealed — candidates are committed (hash-sealed)
against the held-out BEFORE evaluation, then the held-out is revealed at
acceptance time, so a proposer cannot tune to it (DGM "read the test file"
lesson).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..agents.identity import AgentProfile, AgentRegistry, AgentRole
from ..agents.task import TaskStatus, derive_task_status
from ..consensus import ConsensusEngine
from ..exceptions import GateViolation
from ..maat import AnchorSpec, GovernedTaskRegistry


# ---------------------------------------------------------------------------
# Task traces — the raw material of replay
# ---------------------------------------------------------------------------


@dataclass
class Step:
    action: str           # create / claim / submit / validate / close
    actor: str
    payload: dict[str, Any]


@dataclass
class TaskTrace:
    name: str
    objective: str
    db_ref: str | None
    steps: list[Step]
    expect_status: TaskStatus
    # True for probes authored outside the default generator (real harvests,
    # distribution shifts, adversarial minimax probes). The bench's probe
    # sufficiency check requires at least one crafted trace in held_out so a
    # generator-specialised candidate cannot pass on same-distribution data.
    crafted: bool = False


def trace_from_history(name: str, history: list[dict[str, Any]]) -> TaskTrace:
    """Derive a TaskTrace from an EventChain.history() — the lab's playback."""
    objective = ""
    db_ref = None
    steps: list[Step] = []
    expect = TaskStatus.OPEN
    for e in history:
        et = e["event_type"]
        p = e.get("payload", {})
        if et == "task_create":
            objective = p.get("objective", "")
            db_ref = p.get("db_ref")
        elif et in ("task_claim", "task_submit", "task_validate", "task_close"):
            steps.append(Step(et.removeprefix("task_"), e["actor"], p))
    return TaskTrace(name, objective, db_ref, steps, expect)


def sample_traces() -> list[TaskTrace]:
    """Four canonical traces: green path, one rework cycle, escalation, parallel."""
    return [
        TaskTrace(
            "green_path", "routine analysis", None,
            [Step("claim", "worker", {"agent_did": "did:key:zW"}),
             Step("submit", "worker", {"result_ref": "r1"}),
             Step("validate", "reviewer", {"accepted": True}),
             Step("close", "planner", {"outcome": "accepted"})],
            TaskStatus.CLOSED,
        ),
        TaskTrace(
            "one_rework", "flaky fix", "prod-db",
            [Step("claim", "worker", {"agent_did": "did:key:zW"}),
             Step("submit", "worker", {"result_ref": "r1"}),
             Step("validate", "reviewer", {"accepted": False}),
             Step("claim", "worker", {"agent_did": "did:key:zW"}),
             Step("submit", "worker", {"result_ref": "r2"}),
             Step("validate", "reviewer", {"accepted": True}),
             Step("close", "planner", {"outcome": "accepted"})],
            TaskStatus.CLOSED,
        ),
        TaskTrace(
            "escalated", "endless dispute", None,
            [Step("claim", "worker", {"agent_did": "did:key:zW"}),
             Step("submit", "worker", {"result_ref": "r1"}),
             Step("validate", "reviewer", {"accepted": False}),
             Step("claim", "worker", {"agent_did": "did:key:zW"}),
             Step("submit", "worker", {"result_ref": "r2"}),
             Step("validate", "reviewer", {"accepted": False}),
             Step("close", "planner", {"outcome": "cancelled"})],
            TaskStatus.CLOSED,
        ),
        TaskTrace(
            "contended", "shared db work", "prod-db",
            [Step("claim", "w1", {"agent_did": "did:key:zW1"}),
             Step("submit", "w1", {"result_ref": "r1"}),
             Step("validate", "reviewer", {"accepted": True}),
             Step("close", "planner", {"outcome": "accepted"})],
            TaskStatus.CLOSED,
        ),
    ]


# ---------------------------------------------------------------------------
# Planner artifacts — what Phase 2 evolves
# ---------------------------------------------------------------------------


@dataclass
class PlannerSpec:
    """An L1 artifact: how the planner maps traces to task routing decisions."""

    name: str = "default-planner"
    version: int = 1
    # Priority formula knobs: effective = base + w_claim * claimed
    #                              + w_rework * (-rework_loops)
    #                              + w_shared * (db in shared_resources)
    w_claim: float = 1.0
    w_rework: float = 2.0
    w_shared: float = 1.5
    rework_hard_stop: int = 3     # planner proposes escalate at this loop count


def plan(trace: TaskTrace, spec: PlannerSpec) -> list[str]:
    """Deterministic planner: from a trace, produce an action plan.

    The plan is a list of actor-role strings in execution order. A good
    artifact routes contended traces to idle workers and stops endless
    rework; a bad one does the opposite. Fully deterministic given the trace.
    """
    plan: list[str] = []
    loops = 0
    rejected = False
    for s in trace.steps:
        if s.action == "claim":
            if rejected:
                loops += 1
                rejected = False
            plan.append(s.actor)
        elif s.action == "validate" and not s.payload.get("accepted", True):
            rejected = True
        elif s.action == "close":
            if loops >= spec.rework_hard_stop and s.payload.get("outcome") != "cancelled":
                # planner failed to propose escalation: inject the miss
                plan.append("MISSED-ESCALATION")
            plan.append(s.actor)
    return plan


def score_plans(trace: TaskTrace, spec: PlannerSpec) -> dict[str, Any]:
    """Replay the trace under the artifact; return metric deltas vs the
    incumbent default spec. Metrics are counts — deterministic."""
    p = plan(trace, spec)
    missed = sum(1 for x in p if x == "MISSED-ESCALATION")
    routed = len([x for x in p if x.startswith("w") or x in ("worker", "reviewer")])
    return {
        "trace": trace.name,
        "missed_escalations": missed,
        "steps_routed": routed,
        "clean": missed == 0,
    }


# ---------------------------------------------------------------------------
# Commit–reveal held-out bench
# ---------------------------------------------------------------------------


def _hash(x: Any) -> str:
    return hashlib.sha256(
        json.dumps(x, sort_keys=True, default=str).encode()
    ).hexdigest()


@dataclass
class HeldOutBench:
    """Commit–reveal protocol for the hidden scenario set.

    Post-review (C1/H1): the seal is a FROZEN snapshot — candidate content
    hashes and the held-out content hash captured at seal time. Verification
    compares the frozen snapshot, never current mutable state; rotating
    held-out content mid-generation is detected at reveal time."""

    public_traces: list[TaskTrace] = field(default_factory=sample_traces)
    held_out: list[TaskTrace] = field(default_factory=list)
    commitment: str = ""
    revealed: bool = False

    def __post_init__(self) -> None:
        self._frozen_held_out_hash: str | None = None
        self._frozen_candidates: dict[int, str] = {}
        self._commit_seal()

    @staticmethod
    def artifact_content_hash(spec: "PlannerSpec") -> str:
        return _hash({"name": spec.name, "version": spec.version,
                      "w_claim": spec.w_claim, "w_rework": spec.w_rework,
                      "w_shared": spec.w_shared,
                      "rework_hard_stop": spec.rework_hard_stop})

    def _held_out_content_hash(self) -> str:
        return _hash({"held_out": [
            {"name": t.name, "crafted": t.crafted,
             "steps": [{"action": s.action, "actor": s.actor,
                        "payload": s.payload} for s in t.steps]}
            for t in self.held_out]})

    def _payload_hash_from(self, frozen_map: dict[int, str]) -> str:
        payload = {
            "candidates": sorted(
                [{"version": v, "content_hash": h}
                 for v, h in frozen_map.items()], key=lambda d: d["version"]),
            "held_out_hash": self._frozen_held_out_hash,
            "held_out": [t.name for t in self.held_out],
        }
        return _hash(payload)

    def _commit_seal(self, candidates: list["PlannerSpec"] | None = None) -> str:
        self._frozen_held_out_hash = self._held_out_content_hash()
        if candidates is not None:
            self._frozen_candidates = {
                c.version: self.artifact_content_hash(c) for c in candidates}
        self.commitment = self._payload_hash_from(self._frozen_candidates)
        return self.commitment

    def seal(self, candidates: list[PlannerSpec]) -> str:
        """Sealed BEFORE evaluation: versions + CONTENT hashes + held-out."""
        return self._commit_seal(list(candidates))

    def verify_commitment(self, candidates: list[PlannerSpec]) -> bool:
        """Compare against the FROZEN snapshot (C1/H1): any candidate whose
        content changed after sealing, or any unknown version, fails."""
        if not self.commitment:
            return False
        for c in candidates:
            frozen = self._frozen_candidates.get(c.version)
            if frozen is None or frozen != self.artifact_content_hash(c):
                return False
        return self.commitment == self._payload_hash_from(self._frozen_candidates)

    def reveal(self) -> list[TaskTrace]:
        if self._held_out_content_hash() != self._frozen_held_out_hash:
            raise ValueError(
                "constitution: held-out content changed after sealing — "
                "bench rotation is not permitted mid-generation (C1)")
        self.revealed = True
        return list(self.held_out)

    def evaluate(
        self, spec: PlannerSpec, traces: list[TaskTrace] | None = None
    ) -> list[dict[str, Any]]:
        ts = traces if traces is not None else self.held_out
        return [score_plans(t, spec) for t in ts]


# ---------------------------------------------------------------------------
# Artifact lineage + budget
# ---------------------------------------------------------------------------


@dataclass
class ArtifactLineage:
    """Per-artifact lineage with a hard generation budget (quota)."""

    artifact: PlannerSpec
    parent_version: int | None
    generations: int = 0
    budget: int = 10
    trust_score: float = 0.5  # seeded; updated from acceptance outcomes
    exhausted: bool = False

    def spend(self) -> None:
        self.generations += 1
        if self.generations >= self.budget:
            self.exhausted = True

    def record_outcome(self, accepted: bool) -> None:
        """EWMA-style trust update; deterministic."""
        self.trust_score = round(0.8 * self.trust_score + 0.2 * (1.0 if accepted else 0.0), 4)


# ---------------------------------------------------------------------------
# Acceptance routing — who must sign, given diff risk + reputation
# ---------------------------------------------------------------------------

LOW_RISK_FIELDS = {"w_claim", "w_rework", "w_shared"}
MEDIUM_RISK_FIELDS = {"rework_hard_stop"}
HIGH_RISK_FIELDS = {"name"}  # identity change is always board-level


def diff_risk(parent: PlannerSpec, child: PlannerSpec) -> str:
    """Classify the artifact diff; deterministic."""
    diff = {
        f for f in LOW_RISK_FIELDS | MEDIUM_RISK_FIELDS | HIGH_RISK_FIELDS
        if getattr(parent, f) != getattr(child, f)
    }
    if diff & HIGH_RISK_FIELDS:
        return "constitutional"
    if diff & MEDIUM_RISK_FIELDS:
        return "medium"
    return "low"


def acceptance_route(risk: str, trust: float) -> str:
    """Reputation-gated acceptance routing (M4 integration point).

    auto decisions are themselves recorded on-chain as MESSAGE events, so
    delegation never weakens the audit trail — it only saves human minutes.
    """
    if risk == "constitutional":
        return "board_multi_signoff"
    if trust >= 0.9 and risk == "low":
        return "auto_accept"
    if trust >= 0.7:
        return "single_reviewer"
    return "board"


# ---------------------------------------------------------------------------
# The Phase-2 engine
# ---------------------------------------------------------------------------


class ArtifactProposer(Protocol):
    def propose(self, base: PlannerSpec, lineage: list[PlannerSpec]) -> PlannerSpec: ...


@dataclass
class ArtifactScorecard:
    candidate_version: int
    public_results: list[dict[str, Any]] = field(default_factory=list)
    held_out_results: list[dict[str, Any]] = field(default_factory=list)
    commitment_ok: bool = False
    dominates_incumbent: bool = False
    accepted: bool = False

    @property
    def clean_public(self) -> bool:
        return all(r["clean"] for r in self.public_results)

    @property
    def clean_held_out(self) -> bool:
        return all(r["clean"] for r in self.held_out_results)

    @property
    def missed_escalations(self) -> int:
        return sum(r["missed_escalations"] for r in self.public_results + self.held_out_results)

    def dominates(self, other: "ArtifactScorecard") -> bool:
        return (
            self.clean_held_out >= other.clean_held_out
            and self.missed_escalations <= other.missed_escalations
            and (
                self.missed_escalations < other.missed_escalations
                or self.clean_held_out > other.clean_held_out
            )
        )


class SeededArtifactProposer:
    """Deterministic neighborhood search over numeric knobs."""

    def __init__(self, seed: int = 0) -> None:
        self._n = seed

    def propose(self, base: PlannerSpec, lineage: list[PlannerSpec]) -> PlannerSpec:
        self._n += 1
        v = PlannerSpec(**{**base.__dict__, "version": base.version + 1})
        knobs = ["rework_hard_stop", "w_shared", "w_rework"]
        k = knobs[self._n % len(knobs)]
        if k == "rework_hard_stop":
            v.rework_hard_stop = max(1, base.rework_hard_stop + (-1) ** self._n)
        elif k == "w_shared":
            v.w_shared = round(base.w_shared + 0.5 * ((-1) ** self._n), 2)
        else:
            v.w_rework = round(base.w_rework + 0.5 * ((-1) ** (self._n + 1)), 2)
        return v


@dataclass
class ArtifactEvolutionEngine:
    """Phase 2: evolves PlannerSpec artifacts through the same governance
    loop as anchors — constitution (here: numeric bounds), public replay,
    commit–reveal held-out (frozen seal), strict-dominance acceptance,
    budgeted lineage, reputation-routed acceptance with EXECUTED approvals
    (Ed25519 signatures bound to candidate content, scorecard, bench
    commitment, engine identity), every event hash-chained locally and
    anchored into the shared AnchorChain."""

    base_artifact: PlannerSpec = field(default_factory=PlannerSpec)
    bench: HeldOutBench = field(default_factory=HeldOutBench)
    proposer: ArtifactProposer = field(default_factory=SeededArtifactProposer)
    # The anchor's circuit-breaker threshold the planner operates under.
    # Governance caps observable rework loops at threshold-1 (the breaker
    # refuses the threshold-th reject), so a planner whose hard_stop is not
    # strictly below the threshold can never observe its own stop condition
    # — the knob is dead. The constitution therefore rejects such artifacts
    # BEFORE evaluation.
    rework_threshold: int | None = None
    key_ring: Any = None        # approvals.KeyRing; loose type to avoid cycle
    anchor_chain: Any = None    # maat.anchor.AnchorChain
    engine_id: str = "fides-artifact-engine"

    def __post_init__(self) -> None:
        import threading
        self._owner_thread = threading.get_ident()
        self._lock = threading.Lock()
        self._seq = 0
        self._check_constitution(self.base_artifact)
        self.lineage: list[ArtifactLineage] = [
            ArtifactLineage(self.base_artifact, None)
        ]
        self.audit_log: list[dict[str, Any]] = []
        self._commit_seal()
        self._audit(self.base_artifact, "genesis", "init", "genesis")

    def _check_thread(self) -> None:
        import threading
        if self._owner_thread != threading.get_ident():
            raise RuntimeError(
                "engine is single-threaded by constitution: evaluate/accept "
                "must run on the engine's creating thread (M2)")

    def _check_constitution(self, artifact: PlannerSpec) -> None:
        if artifact.rework_hard_stop < 1:
            raise ValueError("constitution: rework_hard_stop must be >= 1")
        if (
            self.rework_threshold is not None
            and artifact.rework_hard_stop >= self.rework_threshold
        ):
            raise ValueError(
                "constitution: rework_hard_stop must be < anchor rework_threshold "
                f"({artifact.rework_hard_stop} >= {self.rework_threshold}); the "
                "circuit breaker caps observable loops at threshold-1, so this "
                "stop condition is unreachable"
            )

    # -- commit–reveal ----------------------------------------------------

    def _commit_seal(self) -> str:
        return self.bench.seal([self.lineage[-1].artifact])

    def _verify_seal(self) -> bool:
        return self.bench.verify_commitment([self.lineage[-1].artifact])

    # -- evaluation -------------------------------------------------------

    def evaluate(self, candidate: PlannerSpec) -> ArtifactScorecard:
        self._check_thread()
        with self._lock:
            self._check_constitution(candidate)
            # commit the candidate pair BEFORE evaluation (DGM lesson);
            # the FROZEN seal covers versions + content hashes + held-out.
            self.bench.seal([self.lineage[-1].artifact, candidate])
            card = ArtifactScorecard(candidate.version)
            card.public_results = self.bench.evaluate(candidate, self.bench.public_traces)
            card.commitment_ok = self._verify_seal()
            incumbent = self.lineage[-1]
            card.dominates_incumbent = card.dominates(
                self._incumbent_card(candidate)
            )
            self._audit(candidate, "evaluate", "scored", "engine")
            return card

    def _scorecard_fingerprint(self, card: ArtifactScorecard) -> str:
        return _hash({
            "v": card.candidate_version,
            "pub": [[r.get("clean"), r.get("missed_escalations")]
                    for r in card.public_results],
            "commitment_ok": card.commitment_ok,
        })

    def _incumbent_card(self, candidate: PlannerSpec) -> ArtifactScorecard:
        """Score the incumbent on the same public set for dominance compare."""
        card = ArtifactScorecard(self.lineage[-1].artifact.version)
        card.public_results = self.bench.evaluate(
            self.lineage[-1].artifact, self.bench.public_traces
        )
        card.held_out_results = self.bench.evaluate(self.lineage[-1].artifact)
        return card

    # -- acceptance --------------------------------------------------------

    def accept(self, candidate: PlannerSpec, card: ArtifactScorecard,
               actor: str,
               signatures: list | None = None) -> str:
        """Returns the acceptance route actually taken; EXECUTES it.

        `actor` is required (no silent default). The route's approval
        policy is enforced: Ed25519 SignedApprovals bound to candidate
        content, scorecard fingerprint, bench commitment, and engine
        identity; board_multi_signoff requires >=2 distinct DIDs, and the
        acceptor may not be one of the approvers (separation of duties).
        Callers' signature errors raise WITHOUT consuming a generation."""
        self._check_thread()
        with self._lock:
            return self._accept_locked(candidate, card, actor, signatures)

    def _accept_locked(self, candidate: PlannerSpec, card: ArtifactScorecard,
                       actor: str, signatures: list | None) -> str:
        if not card.commitment_ok or not self._verify_seal():
            raise ValueError("commit–reveal seal broken: candidate not committed")
        # fresh content verification against the frozen seal (H1): a
        # same-version impostor with different field values fails here
        if not self.bench.verify_commitment([self.lineage[-1].artifact, candidate]):
            raise ValueError(
                "commit–reveal seal broken: candidate content changed "
                "after sealing (H1)")
        incumbent = self.lineage[-1]
        risk = diff_risk(incumbent.artifact, candidate)
        route = acceptance_route(risk, incumbent.trust_score)

        # executed routing: signatures verified BEFORE the reveal
        from .approvals import APPROVAL_POLICY, KeyRing, acceptance_domain
        policy = APPROVAL_POLICY[route]
        domain = acceptance_domain(
            engine_id=self.engine_id,
            candidate_hash=HeldOutBench.artifact_content_hash(candidate),
            scorecard_hash=self._scorecard_fingerprint(card),
            bench_commitment=self.bench.commitment or "")
        sigs = list(signatures or [])
        if len(sigs) < policy["min_signers"]:
            raise ValueError(
                f"route={route} requires {policy['min_signers']} signed "
                f"approval(s); got {len(sigs)} — the route is executed, "
                "not merely computed (C2)")
        ring = self.key_ring or KeyRing()
        seen_dids: set[str] = set()
        for sig in sigs:
            if not ring.verify(sig, domain):
                raise ValueError(
                    "constitution: approval signature invalid or bound to a "
                    "different acceptance domain (C2)")
            seen_dids.add(sig.did)
        if len(seen_dids) < policy["min_distinct_dids"]:
            raise ValueError(
                f"route={route} requires {policy['min_distinct_dids']} "
                f"distinct signer DIDs; got {len(seen_dids)}")
        if policy["reviewer_must_differ"] and actor in seen_dids:
            raise ValueError(
                "constitution: separation of duties — the accepting actor "
                f"cannot be one of the approvers for route {route}")

        # held-out reveal + scoring happens HERE, post-commitment
        if self.bench.revealed:
            raise ValueError("bench already revealed for this generation")
        self.bench.reveal()
        card.held_out_results = self.bench.evaluate(candidate)
        incumbent_public = self.bench.evaluate(incumbent.artifact, self.bench.public_traces)
        incumbent_held = self.bench.evaluate(incumbent.artifact)
        card_ok = all(r["clean"] for r in card.held_out_results)
        inc_ok = all(r["clean"] for r in incumbent_held)
        card_missed = sum(r["missed_escalations"] for r in card.public_results + card.held_out_results)
        inc_missed = sum(r["missed_escalations"] for r in incumbent_public + incumbent_held)
        dominates = card_ok >= inc_ok and (card_ok > inc_ok or card_missed < inc_missed)
        card.dominates_incumbent = dominates

        if route in ("auto_accept", "single_reviewer") and not dominates:
            # delegation does not waive the dominance gate
            route = "board"
            card.dominates_incumbent = False

        if not card.dominates_incumbent:
            incumbent.record_outcome(accepted=False)
            self._audit(candidate, route, "rejected", actor)
            self.bench.revealed = False  # bench resets for the next generation
            self._commit_seal()
            raise ValueError(
                "candidate rejected: does not dominate on held-out "
                "(details in the anchored audit record)")

        self._audit(candidate, route, "accepted", actor)
        incumbent.record_outcome(accepted=True)
        incumbent.spend()
        new_lineage = ArtifactLineage(
            candidate, parent_version=incumbent.artifact.version,
            trust_score=round(0.5 * incumbent.trust_score + 0.5, 4),
            budget=incumbent.budget,
        )
        new_lineage.generations = incumbent.generations
        self.lineage.append(new_lineage)
        self.bench.revealed = False  # bench resets for the next generation
        self._commit_seal()
        return route

    def _audit(self, candidate: PlannerSpec, route: str, outcome: str,
               actor: str) -> None:
        """Hash-chained local audit entry PLUS anchoring into the shared
        AnchorChain — the chain is the record; the log is a projection (C1).
        Entries carry seq/UTC/actor so 'who approved what, when' is
        answerable from the chain."""
        import hashlib
        from datetime import datetime, timezone
        self._seq += 1
        entry: dict[str, Any] = {
            "seq": self._seq,
            "candidate_version": candidate.version,
            "route": route,
            "outcome": outcome,
            "actor": actor,
            "risk": diff_risk(self.lineage[-1].artifact, candidate),
            "trust": self.lineage[-1].trust_score,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        prev = self.audit_log[-1]["hash"] if self.audit_log else "GENESIS"
        entry["prev"] = prev
        entry["hash"] = hashlib.sha256(
            json.dumps({**entry, "prev": prev}, sort_keys=True,
                       default=str).encode()).hexdigest()
        self.audit_log.append(entry)
        if self.anchor_chain is not None:
            self.anchor_chain.append_event(entry, actor=actor)

    def verify_audit_chain(self) -> bool:
        import hashlib
        for i, entry in enumerate(self.audit_log):
            prev = self.audit_log[i - 1]["hash"] if i else "GENESIS"
            canonical = json.dumps(
                {k: v for k, v in entry.items() if k != "hash"},
                sort_keys=True, default=str)
            if entry["prev"] != prev:
                return False
            if entry["hash"] != hashlib.sha256(canonical.encode()).hexdigest():
                return False
        if self.anchor_chain is not None:
            anchored = [e for e in self.anchor_chain.revisions
                        if "event" in e]
            if len(anchored) != len(self.audit_log):
                return False
            for entry, a in zip(self.audit_log, anchored):
                if a["event"] != entry:
                    return False
        return True

    def propose(self) -> PlannerSpec:
        incumbent = self.lineage[-1]
        if incumbent.exhausted:
            raise ValueError(f"budget exhausted for lineage of v{incumbent.artifact.version}")
        return self.proposer.propose(incumbent.artifact, [l.artifact for l in self.lineage])

    def report(self) -> list[dict[str, Any]]:
        return [
            {
                "version": l.artifact.version,
                "parent": l.parent_version,
                "trust": l.trust_score,
                "generations": l.generations,
                "budget": l.budget,
                "exhausted": l.exhausted,
            }
            for l in self.lineage
        ]
