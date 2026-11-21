"""GovernedTaskRegistry — TaskRegistry behind the deterministic gate layer.

Every lifecycle method consults the GateKeeper *before* any event is appended;
a blocked handoff raises GateViolation and leaves the chain untouched (the
chain records facts, not attempted facts). On success, gate-audit messages are
appended so the governance trail is replayable from the chain.
"""

from __future__ import annotations

from typing import Any, Literal

from ..agents.identity import AgentRegistry, chain_kind
from ..agents.task import Task, TaskRegistry, TaskStatus, derive_task_status
from ..consensus import ConsensusEngine
from ..exceptions import GateViolation
from ..models import Event, EventType
from .anchor import AnchorChain, AnchorSpec
from .gates import GateKeeper, HandoffContext, current_claim_holder
from .locks import ResourceLockManager


class GovernedTaskRegistry(TaskRegistry):
    def __init__(
        self,
        engine: ConsensusEngine | None = None,
        anchor: AnchorSpec | None = None,
        agents: AgentRegistry | None = None,
        audit_gates: bool = True,
    ) -> None:
        super().__init__(engine)
        self.anchor = anchor or AnchorSpec()
        self.agents = agents or AgentRegistry(self.engine)
        self.locks = ResourceLockManager(set(self.anchor.shared_resources))
        self.gates = GateKeeper(self.anchor, self.locks, self.agents)
        self.audit_gates = audit_gates
        self.anchor_chain = AnchorChain(self.anchor.anchor_id)
        self.anchor_chain.append(self.anchor)

    # ------------------------------------------------------------------
    # Anchor lifecycle (versioned, hash-anchored — the Maat lesson)
    # ------------------------------------------------------------------

    def set_anchor(self, anchor: AnchorSpec, actor: str = "governance") -> None:
        """Rotate the governing contract; the revision is anchored first."""
        if anchor.anchor_id != self.anchor.anchor_id:
            raise ValueError("anchor_id mismatch")
        self.anchor_chain.append(anchor, actor=actor)
        self.anchor = anchor
        self.locks.shared_resources = set(anchor.shared_resources)
        self.gates.anchor = anchor

    def verify_anchor_history(self) -> bool:
        return self.anchor_chain.verify()

    # ------------------------------------------------------------------
    # Governed lifecycle
    # ------------------------------------------------------------------

    def _check(self, ctx: HandoffContext) -> None:
        verdict = self.gates.validate(ctx)
        if not verdict.allowed:
            raise GateViolation(verdict.gate, verdict.reason)

    def _audit(self, task_id: str, verdict_gate: str, detail: str, actor: str) -> None:
        if not self.audit_gates:
            return
        chain = self.engine.chains.get(task_id)
        if chain is None:
            return
        chain.append(
            Event(
                concept_id=task_id,
                event_type=EventType.MESSAGE,
                actor=actor,
                reasoning="maat gate audit",
                payload={"gate": verdict_gate, "detail": detail},
            )
        )

    def _ctx(self, task_id: str, et: EventType, actor: str, payload: dict) -> HandoffContext:
        chain = self.engine.chains.get(task_id)
        current = derive_task_status(chain.events) if chain else TaskStatus.OPEN
        return HandoffContext(task_id, et, actor, payload, current)

    def create_task(
        self,
        objective: str,
        required_capabilities: list[str] | None = None,
        created_by: str = "planner",
        parent_task_id: str | None = None,
        priority: int = 0,
        scope: str = "public",
        tenant: str | None = None,
        input_ref: str | None = None,
        db_ref: str | None = None,
    ) -> Task:
        probe = HandoffContext(
            task_id="<new>",
            event_type=EventType.TASK_CREATE,
            actor=created_by,
            payload={
                "required_capabilities": required_capabilities or [],
                "parent_task_id": parent_task_id,
                "db_ref": db_ref,
            },
            current_status=TaskStatus.OPEN,
        )
        self._check(probe)
        task = super().create_task(
            objective,
            required_capabilities=required_capabilities,
            created_by=created_by,
            parent_task_id=parent_task_id,
            priority=priority,
            scope=scope,
            tenant=tenant,
            input_ref=input_ref,
            db_ref=db_ref,
        )
        self._audit(task.task_id, "lineage", "task created", created_by)
        return task

    def claim(self, task_id: str, agent_did: str) -> Event:
        chain = self.engine.chains[task_id]
        db_ref = chain.events[0].payload.get("db_ref")
        ctx = self._ctx(
            task_id,
            EventType.TASK_CLAIM,
            agent_did,
            {"db_ref": db_ref},
        )
        self._check(ctx)
        event = super().claim(task_id, agent_did)
        self._audit(task_id, "all", "claim permitted", agent_did)
        return event

    def submit(
        self,
        task_id: str,
        agent_did: str,
        result_ref: str,
        summary: str = "",
        confidence: float = 0.5,
    ) -> Event:
        chain = self.engine.chains[task_id]
        holder = current_claim_holder(chain.events)
        if holder is not None and agent_did != holder:
            raise GateViolation(
                "identity", f"submit by non-holder: bound to {holder}"
            )
        ctx = self._ctx(
            task_id,
            EventType.TASK_SUBMIT,
            agent_did,
            {"result_ref": result_ref},
        )
        self._check(ctx)
        event = super().submit(task_id, agent_did, result_ref, summary, confidence)
        self._audit(task_id, "all", "submit permitted", agent_did)
        return event

    def validate_result(
        self,
        task_id: str,
        validator_actor: str,
        accepted: bool,
        confidence: float = 0.8,
        critique: str = "",
    ) -> Event:
        chain = self.engine.chains[task_id]
        if self.anchor.validator_not_submitter:
            holder = current_claim_holder(chain.events)
            if holder is not None and validator_actor == holder:
                raise GateViolation(
                    "identity", "validator must not be the submitting agent (B3 analog)"
                )
        ctx = self._ctx(
            task_id,
            EventType.TASK_VALIDATE,
            validator_actor,
            {"accepted": accepted},
        )
        self._check(ctx)
        event = super().validate_result(
            task_id, validator_actor, accepted, confidence, critique
        )
        if not accepted:
            # rejection releases the execution binding AND the resource lock:
            # rework must re-claim and re-compete for the shared object.
            self.locks.release_for_task(task_id)
        self._audit(task_id, "all", f"validate accepted={accepted}", validator_actor)
        return event

    def close(
        self,
        task_id: str,
        actor: str,
        outcome: Literal["accepted", "rejected", "cancelled"],
        reason: str = "",
    ) -> Event:
        ctx = self._ctx(task_id, EventType.TASK_CLOSE, actor, {"outcome": outcome})
        self._check(ctx)
        if outcome == "cancelled":
            # Operator takeover: the governed layer owns this path (the gate
            # already validated the widened state-machine semantics).
            event = self._append(
                task_id, EventType.TASK_CLOSE, actor, {"outcome": outcome, "reason": reason}
            )
        else:
            event = super().close(task_id, actor, outcome, reason)
        self.locks.release_for_task(task_id)
        self._audit(task_id, "all", f"close outcome={outcome}", actor)
        if self.anchor.cascade_cancel:
            self._cascade_cancel(task_id, actor)
        return event

    def _cascade_cancel(self, parent_id: str, actor: str) -> int:
        """Root-void semantics: closing a parent voids every live descendant."""
        cancelled = 0
        for cid, chain in list(self.engine.chains.items()):
            if chain_kind(chain) != "task":
                continue
            genesis = chain.events[0].payload
            if genesis.get("parent_task_id") != parent_id:
                continue
            if derive_task_status(chain.events) == TaskStatus.CLOSED:
                continue
            # Recurse through self.close: cancelled closes are governed
            # (operator-takeover) closes and cascade further descendants.
            self.close(cid, actor, "cancelled", reason=f"cascade from {parent_id}")
            cancelled += 1
        return cancelled
