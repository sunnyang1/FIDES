"""Maat governance validation suite — poison tests against adl-lite.

Maps 1:1 to the validation-lab findings:
  poison 2 (orphan ticket)   -> lineage gate
  rework ping-pong           -> circuit_breaker gate (deterministic threshold)
  double-claim / wrong-owner -> identity gate + submit-holder binding
  shared-resource contention -> resource gate (Sui-style shared objects)
  anchor tampering           -> AnchorChain hash-link verification
Plus green-path and audit-trail tests.
"""

from __future__ import annotations

import pytest

from adl_lite.agents.identity import AgentProfile, AgentRegistry, AgentRole, AgentStatus
from adl_lite.agents.task import TaskRegistry, TaskStatus
from adl_lite.consensus import ConsensusEngine
from adl_lite.exceptions import GateViolation
from adl_lite.maat import AnchorSpec, GovernedTaskRegistry
from adl_lite.maat.anchor import AnchorChain

ROOT_DID = "did:key:zRoot"


def make_agents(engine, *names: str) -> dict[str, str]:
    """Register agents and activate them via the admin trust-root path (P0-1)."""
    reg = AgentRegistry(
        engine, admin_public_keys={ROOT_DID: "key"}, admin_calls_allowed=True
    )
    dids = {}
    for name in names:
        did = f"did:key:z{name}"
        reg.register_agent(AgentProfile(did=did, role=AgentRole.DISCOVERER, name=name))
        reg.validate_agent(did, ROOT_DID, reason="bootstrap", admin=True)
        dids[name] = did
    assert all(reg.agent_status(d) == AgentStatus.ACTIVE for d in dids.values())
    return dids


def test_green_path_full_lifecycle():
    eng = ConsensusEngine(dev_mode=True)
    dids = make_agents(eng, "Alice", "Val")
    reg = GovernedTaskRegistry(eng, AnchorSpec(rework_threshold=3), agents=AgentRegistry(eng))
    t = reg.create_task("repair login bug", required_capabilities=["related-to"])
    reg.claim(t.task_id, dids["Alice"])
    reg.submit(t.task_id, dids["Alice"], "cap-fix-v1")
    reg.validate_result(t.task_id, dids["Val"], accepted=True)
    reg.close(t.task_id, "planner", "accepted")
    assert reg.get_task(t.task_id).status == TaskStatus.CLOSED
    assert eng.chains[t.task_id].verify_integrity()


def test_poison2_orphan_parent_blocked():
    """Validation-lab poison 2: forged parent id must not mint a live task."""
    eng = ConsensusEngine(dev_mode=True)
    reg = GovernedTaskRegistry(eng, agents=AgentRegistry(eng))
    with pytest.raises(GateViolation) as exc:
        reg.create_task("evil child", parent_task_id="task:nonexistent")
    assert exc.value.gate == "lineage"
    # nothing was created
    assert all(
        c.events[0].payload.get("objective") != "evil child" for c in eng.chains.values()
    )


def test_circuit_breaker_halts_rework_pingpong():
    """Experiment B on real code: threshold-th reject is refused; escalation close works."""
    eng = ConsensusEngine(dev_mode=True)
    dids = make_agents(eng, "A1", "V1")
    reg = GovernedTaskRegistry(
        eng, AnchorSpec(rework_threshold=3), agents=AgentRegistry(eng)
    )
    t = reg.create_task("flaky fix", db_ref="prod-db")
    reg.claim(t.task_id, dids["A1"])
    reg.submit(t.task_id, dids["A1"], "r1")
    reg.validate_result(t.task_id, dids["V1"], accepted=False)  # loop 1
    reg.claim(t.task_id, dids["A1"])
    reg.submit(t.task_id, dids["A1"], "r2")
    reg.validate_result(t.task_id, dids["V1"], accepted=False)  # loop 2
    # third reject would complete loop 3 == threshold -> refused
    reg.claim(t.task_id, dids["A1"])
    reg.submit(t.task_id, dids["A1"], "r3")
    with pytest.raises(GateViolation) as exc:
        reg.validate_result(t.task_id, dids["V1"], accepted=False)
    assert exc.value.gate == "circuit_breaker"
    # human escalation path
    reg.close(t.task_id, "planner", "cancelled", reason="escalated: rework circuit")
    assert reg.get_task(t.task_id).status == TaskStatus.CLOSED
    assert eng.chains[t.task_id].verify_integrity()


def test_submit_by_non_holder_blocked():
    """Double-claim / wrong-owner: only the bound claim holder may submit."""
    eng = ConsensusEngine(dev_mode=True)
    dids = make_agents(eng, "Alice", "Mallory")
    reg = GovernedTaskRegistry(eng, agents=AgentRegistry(eng))
    t = reg.create_task("sensitive change")
    reg.claim(t.task_id, dids["Alice"])
    with pytest.raises(GateViolation) as exc:
        reg.submit(t.task_id, dids["Mallory"], "forged-result")
    assert exc.value.gate == "identity"
    # chain untouched by the attempt
    types = [e.event_type.value for e in eng.chains[t.task_id].events]
    assert "task_submit" not in types


def test_self_validation_blocked():
    eng = ConsensusEngine(dev_mode=True)
    dids = make_agents(eng, "Alice")
    reg = GovernedTaskRegistry(eng, agents=AgentRegistry(eng))
    t = reg.create_task("self-grading")
    reg.claim(t.task_id, dids["Alice"])
    reg.submit(t.task_id, dids["Alice"], "r")
    with pytest.raises(GateViolation) as exc:
        reg.validate_result(t.task_id, dids["Alice"], accepted=True)
    assert exc.value.gate == "identity"


def test_shared_resource_contention():
    """Experiment C resource gate: shared db serializes, exclusive flows free."""
    eng = ConsensusEngine(dev_mode=True)
    dids = make_agents(eng, "A1", "A2", "V1")
    anchor = AnchorSpec(shared_resources=["prod-db"], rework_threshold=5)
    reg = GovernedTaskRegistry(eng, anchor=anchor, agents=AgentRegistry(eng))

    t1 = reg.create_task("schema migration", db_ref="prod-db")
    t2 = reg.create_task("hotfix", db_ref="prod-db")
    t3 = reg.create_task("docs update", db_ref="wiki")  # exclusive object

    reg.claim(t1.task_id, dids["A1"])  # acquires prod-db
    reg.claim(t3.task_id, dids["A2"])  # lock-free: proceeds in parallel
    with pytest.raises(GateViolation) as exc:
        reg.claim(t2.task_id, dids["A2"])  # prod-db held by A1
    assert exc.value.gate == "resource"

    # t1 completes -> t2 may now compete
    reg.submit(t1.task_id, dids["A1"], "mig-v1")
    reg.validate_result(t1.task_id, dids["V1"], accepted=True)
    reg.close(t1.task_id, "planner", "accepted")
    reg.claim(t2.task_id, dids["A2"])  # lock released on close
    assert reg.locks.holder_of("prod-db") == (dids["A2"], t2.task_id)


def test_rejection_releases_shared_lock_for_rework():
    eng = ConsensusEngine(dev_mode=True)
    dids = make_agents(eng, "A1", "A2", "V1")
    anchor = AnchorSpec(shared_resources=["prod-db"], rework_threshold=9)
    reg = GovernedTaskRegistry(eng, anchor=anchor, agents=AgentRegistry(eng))
    t = reg.create_task("needs rework", db_ref="prod-db")
    reg.claim(t.task_id, dids["A1"])
    reg.submit(t.task_id, dids["A1"], "r1")
    reg.validate_result(t.task_id, dids["V1"], accepted=False)  # releases lock
    assert reg.locks.holder_of("prod-db") is None
    reg.claim(t.task_id, dids["A2"])  # another agent may now take it
    assert reg.locks.holder_of("prod-db") == (dids["A2"], t.task_id)


def test_cascade_cancel_root_void():
    eng = ConsensusEngine(dev_mode=True)
    dids = make_agents(eng, "A1")
    reg = GovernedTaskRegistry(eng, agents=AgentRegistry(eng))
    root = reg.create_task("epic: rebuild auth")
    child = reg.create_task("subtask: migrate sessions", parent_task_id=root.task_id)
    grandchild = reg.create_task("subsub: rotate keys", parent_task_id=child.task_id)
    reg.claim(child.task_id, dids["A1"])
    reg.close(root.task_id, "planner", "cancelled", reason="scope change")
    assert reg.get_task(child.task_id).status == TaskStatus.CLOSED
    assert reg.get_task(grandchild.task_id).status == TaskStatus.CLOSED
    assert eng.chains[child.task_id].verify_integrity()


def test_anchor_history_tamper_evident():
    """Maat lesson: the contract is part of the system."""
    anchor = AnchorSpec(rework_threshold=3, shared_resources=["prod-db"])
    chain = AnchorChain()
    chain.append(anchor)
    v2 = anchor.model_copy(update={"version": 2, "rework_threshold": 2})
    chain.append(v2)
    assert chain.verify()
    # attacker rewrites history: lower the threshold in revision 1
    chain.revisions[0]["anchor_hash"] = "0" * 64
    assert not chain.verify()


def test_anchor_rotation_governs_new_verdicts():
    eng = ConsensusEngine(dev_mode=True)
    dids = make_agents(eng, "Ghost")
    reg = GovernedTaskRegistry(eng, agents=AgentRegistry(eng))
    # loosen: identity gate off
    relaxed = reg.anchor.model_copy(update={"version": 2, "enforce_identity": False})
    reg.set_anchor(relaxed, actor="board")
    t = reg.create_task("no identity enforcement")
    reg.claim(t.task_id, "did:key:zNobody")  # unregistered actor passes
    assert reg.verify_anchor_history()


def test_gate_audit_trail_on_chain():
    eng = ConsensusEngine(dev_mode=True)
    dids = make_agents(eng, "A1", "V1")
    reg = GovernedTaskRegistry(eng, agents=AgentRegistry(eng))
    t = reg.create_task("audited task")
    reg.claim(t.task_id, dids["A1"])
    msgs = [e for e in eng.chains[t.task_id].events if e.event_type.value == "message"]
    assert msgs and all("gate" in e.payload for e in msgs)


def test_baseline_registry_unaffected():
    """Governance is opt-in: baseline TaskRegistry still works without gates."""
    eng = ConsensusEngine(dev_mode=True)
    reg = TaskRegistry(eng)
    t = reg.create_task("legacy", parent_task_id="task:nonexistent")  # no guard upstream
    assert t.task_id in eng.chains
