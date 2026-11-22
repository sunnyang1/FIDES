"""Deterministic seven-gate handoff validator (Maat-style, ADL-adapted).

Sits at task handoff boundaries (create / claim / submit / validate / close)
and compares each prospective event against the versioned AnchorSpec. No
language model is in the validation or scoring path: for a fixed engine,
fixed anchor, and identical chain state, every verdict is reproducible.

Gate taxonomy (adapted to ADL Lite's task EventChains; the *idea* of a
fixed multi-check rubric follows Maat, the checks themselves are ADL's):
  1. structure       — required payload fields for the event type
  2. vocabulary      — required_capabilities ∈ known capability vocabulary
  3. state_machine   — transition ∈ _TASK_TRANSITIONS(current)
  4. identity        — DID actors are registered, ACTIVE agents (system
                       roles authenticate at the API layer)
  5. lineage         — parent_task_id exists and is a task chain
  6. resource        — shared-object lock held before claim executes
  7. circuit_breaker — rework loops halt at anchor.rework_threshold
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..agents.identity import AgentRegistry, AgentStatus, chain_kind
from ..agents.task import TaskStatus, _TASK_TRANSITIONS
from ..did_resolver import is_did
from ..models import Event, EventType
from .anchor import AnchorSpec
from .locks import LockVerdict, ResourceLockManager


@dataclass
class GateVerdict:
    gate: str
    allowed: bool
    reason: str = ""


@dataclass
class HandoffContext:
    """Everything the gates need to know about one prospective handoff."""

    task_id: str
    event_type: EventType
    actor: str
    payload: dict[str, Any]
    current_status: TaskStatus


def count_rework_loops(events: list[Event]) -> int:
    """Deterministic fold: REJECTED → next CLAIM cycles (code, not model)."""
    status = TaskStatus.OPEN
    loops = 0
    for e in events:
        s = None
        if e.event_type == EventType.TASK_CREATE:
            s = TaskStatus.OPEN
        elif e.event_type == EventType.TASK_CLAIM:
            s = TaskStatus.IN_PROGRESS
        elif e.event_type == EventType.TASK_VALIDATE:
            s = TaskStatus.VALIDATED if e.payload.get("accepted") else TaskStatus.REJECTED
        if s is not None:
            if s == TaskStatus.IN_PROGRESS and status == TaskStatus.REJECTED:
                loops += 1
            status = s
    return loops


def current_claim_holder(events: list[Event]) -> str | None:
    """The agent bound by the latest TASK_CLAIM, if any."""
    holder: str | None = None
    for e in events:
        if e.event_type == EventType.TASK_CLAIM:
            holder = e.payload.get("agent_did")
        elif e.event_type == EventType.TASK_VALIDATE and not e.payload.get("accepted"):
            holder = None  # rejection releases the binding (rework re-binds)
    return holder


class GateKeeper:
    """Runs the deterministic gate sequence against the current anchor."""

    def __init__(
        self,
        anchor: AnchorSpec,
        locks: ResourceLockManager,
        agents: AgentRegistry | None = None,
    ) -> None:
        self.anchor = anchor
        self.locks = locks
        self.agents = agents
        self.verdict_log: list[GateVerdict] = []

    def _record(self, v: GateVerdict) -> GateVerdict:
        self.verdict_log.append(v)
        return v

    # -- individual gates ------------------------------------------------

    def g1_structure(self, ctx: HandoffContext) -> GateVerdict:
        required = self.anchor.required_payload_fields.get(ctx.event_type.value, [])
        missing = [
            f
            for f in required
            if f not in ctx.payload or ctx.payload[f] is None
            or (isinstance(ctx.payload[f], str) and not ctx.payload[f].strip())
        ]
        if missing:
            return GateVerdict("structure", False, f"missing payload fields: {missing}")
        return GateVerdict("structure", True, "payload complete")

    def g2_vocabulary(self, ctx: HandoffContext) -> GateVerdict:
        caps = ctx.payload.get("required_capabilities") or []
        unknown = [c for c in caps if not self._known_capability(c)]
        if unknown:
            return GateVerdict("vocabulary", False, f"unknown capabilities: {unknown}")
        return GateVerdict("vocabulary", True, "vocabulary OK")

    def _known_capability(self, capability: str) -> bool:
        if self.agents is not None:
            from ..ontology import default_ontology

            om = default_ontology()
            if om.validate_predicate(capability):
                return True
            return any(
                chain_kind(self.agents.engine.chains[c]) == "discovery" and c == capability
                for c in self.agents.engine.chains
            )
        return True  # no registry attached: gate degenerates to pass

    def g3_state_machine(self, ctx: HandoffContext) -> GateVerdict:
        target = {
            EventType.TASK_ASSIGN: TaskStatus.ASSIGNED,
            EventType.TASK_CLAIM: TaskStatus.IN_PROGRESS,
            EventType.TASK_SUBMIT: TaskStatus.SUBMITTED,
            EventType.TASK_CLOSE: TaskStatus.CLOSED,
        }.get(ctx.event_type)
        if ctx.event_type == EventType.TASK_VALIDATE:
            target = (
                TaskStatus.VALIDATED
                if ctx.payload.get("accepted")
                else TaskStatus.REJECTED
            )
        if target is None:
            return GateVerdict("state_machine", True, "no state change")
        permitted = target in _TASK_TRANSITIONS[ctx.current_status]
        # Human-override semantics: a *cancelled* close is an operator
        # takeover — admissible from any live state (escalation, cascade
        # void, budget halt). accepted/rejected closes stay on the baseline
        # machine.
        if (
            not permitted
            and ctx.event_type == EventType.TASK_CLOSE
            and ctx.payload.get("outcome") == "cancelled"
            and ctx.current_status != TaskStatus.CLOSED
        ):
            permitted = True
        if not permitted:
            return GateVerdict(
                "state_machine",
                False,
                f"transition {ctx.current_status.value} -> {target.value} not permitted",
            )
        return GateVerdict("state_machine", True, "transition permitted")

    def g4_identity(self, ctx: HandoffContext) -> GateVerdict:
        if not self.anchor.enforce_identity:
            return GateVerdict("identity", True, "identity gate disabled")
        if self.agents is None:
            return GateVerdict("identity", True, "no agent registry: gate degenerates")
        if not is_did(ctx.actor):
            # System roles (planner/governance/...) authenticate at the API
            # layer (admin JWT), not at the agent-identity layer.
            return GateVerdict("identity", True, "system role: API-layer authentication")
        status = self.agents.agent_status(ctx.actor)
        if status != AgentStatus.ACTIVE:
            return GateVerdict(
                "identity", False, f"actor {ctx.actor} is not an ACTIVE agent (status={status.value})"
            )
        return GateVerdict("identity", True, "actor ACTIVE")

    def g5_lineage(self, ctx: HandoffContext) -> GateVerdict:
        if not self.anchor.enforce_lineage:
            return GateVerdict("lineage", True, "lineage gate disabled")
        parent = ctx.payload.get("parent_task_id")
        if not parent:
            return GateVerdict("lineage", True, "no parent")
        chain = self._chain_of(parent)
        if chain is None:
            return GateVerdict("lineage", False, f"parent_task_id {parent} does not exist")
        if chain_kind(chain) != "task":
            return GateVerdict(
                "lineage", False, f"parent_task_id {parent} is a {chain_kind(chain)} chain"
            )
        return GateVerdict("lineage", True, "parent verified")

    def _chain_of(self, concept_id: str):
        if self.agents is not None:
            return self.agents.engine.chains.get(concept_id)
        return None

    def g6_resource(self, ctx: HandoffContext) -> GateVerdict:
        db_ref = ctx.payload.get("db_ref")
        if ctx.event_type != EventType.TASK_CLAIM:
            return GateVerdict("resource", True, "claim-only gate")
        if not self.locks.is_shared(db_ref):
            return GateVerdict("resource", True, "exclusive object: lock-free")
        verdict: LockVerdict = self.locks.acquire(db_ref, ctx.actor, ctx.task_id)
        if not verdict.allowed:
            return GateVerdict("resource", False, verdict.reason)
        return GateVerdict("resource", True, "shared object lock acquired")

    def g7_circuit_breaker(self, ctx: HandoffContext) -> GateVerdict:
        if not self.anchor.enforce_circuit_breaker:
            return GateVerdict("circuit_breaker", True, "circuit breaker disabled")
        if ctx.event_type != EventType.TASK_VALIDATE or ctx.payload.get("accepted"):
            return GateVerdict("circuit_breaker", True, "reject-only gate")
        chain = self._chain_of(ctx.task_id)
        if chain is None:
            return GateVerdict("circuit_breaker", True, "no chain")
        loops = count_rework_loops(chain.events)
        if loops + 1 >= self.anchor.rework_threshold:
            return GateVerdict(
                "circuit_breaker",
                False,
                f"rework loop {loops + 1} would reach threshold "
                f"{self.anchor.rework_threshold}: reject refused, escalate via close",
            )
        return GateVerdict("circuit_breaker", True, f"loop {loops + 1} < threshold")

    # -- sequence ---------------------------------------------------------

    def validate(self, ctx: HandoffContext) -> GateVerdict:
        """Run gates in fixed order; first block wins (short-circuit).

        Order is itself part of the deterministic contract: cheap structural
        gates run before gates that mutate state (resource acquisition).
        """
        for gate in (
            self.g1_structure,
            self.g2_vocabulary,
            self.g3_state_machine,
            self.g4_identity,
            self.g5_lineage,
            self.g7_circuit_breaker,
            self.g6_resource,  # last: may acquire a lock
        ):
            v = gate(ctx)
            self._record(v)
            if not v.allowed:
                return v
        return GateVerdict("all", True, "handoff permitted")
