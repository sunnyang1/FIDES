"""FIDES Phase 2 experiment — real-trace evolution run.

Generates TaskTraces from live GovernedTaskRegistry EventChains (not
hand-written fixtures), splits public/held-out, then runs the
ArtifactEvolutionEngine to quantify:

  1. generations_to_accept     — how many proposals the loop needs
  2. public vs held-out gap    — proxy-gain rate: candidates that look
                                 better on public but fail held-out
  3. trust trajectory          — EWMA trust as outcomes accumulate
  4. routing distribution      — where acceptance authority actually sat
  5. anti-overfitting control  — the commit–reveal seal binds candidates
                                 to a pre-evaluation commitment

Run:  python3 experiments/exp_p2_real_traces.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adl_lite.agents.identity import AgentProfile, AgentRegistry, AgentRole
from adl_lite.consensus import ConsensusEngine
from adl_lite.evolution.replay import (
    ArtifactEvolutionEngine,
    HeldOutBench,
    PlannerSpec,
    TaskTrace,
    trace_from_history,
)
from adl_lite.maat import AnchorSpec, GovernedTaskRegistry

ROOT_DID = "did:key:zRoot"
OUT = Path(__file__).resolve().parent.parent / "experiments" / "exp_p2_results.json"


# ---------------------------------------------------------------------------
# Live scenario runs — traces come from real governed EventChains
# ---------------------------------------------------------------------------


def make_agents(engine, *names: str) -> dict[str, str]:
    reg = AgentRegistry(
        engine, admin_public_keys={ROOT_DID: "key"}, admin_calls_allowed=True
    )
    dids = {}
    for name in names:
        did = f"did:key:z{name}"
        reg.register_agent(AgentProfile(did=did, role=AgentRole.DISCOVERER, name=name))
        reg.validate_agent(did, ROOT_DID, reason="bootstrap", admin=True)
        dids[name] = did
    return dids


def fresh_registry() -> GovernedTaskRegistry:
    engine = ConsensusEngine(dev_mode=True)
    dids = make_agents(engine, "W1", "W2", "V1")
    reg = GovernedTaskRegistry(
        engine,
        AnchorSpec(rework_threshold=5, shared_resources=["prod-db"]),
        agents=AgentRegistry(engine),
    )
    return reg, dids


def run_green_path(reg, dids) -> str:
    t = reg.create_task("routine analysis")
    reg.claim(t.task_id, dids["W1"])
    reg.submit(t.task_id, dids["W1"], "r1")
    reg.validate_result(t.task_id, dids["V1"], accepted=True)
    reg.close(t.task_id, "planner", "accepted")
    return t.task_id


def run_one_rework(reg, dids) -> str:
    t = reg.create_task("flaky fix", db_ref="prod-db")
    reg.claim(t.task_id, dids["W1"])
    reg.submit(t.task_id, dids["W1"], "r1")
    reg.validate_result(t.task_id, dids["V1"], accepted=False)   # loop 1
    reg.claim(t.task_id, dids["W1"])
    reg.submit(t.task_id, dids["W1"], "r2")
    reg.validate_result(t.task_id, dids["V1"], accepted=True)
    reg.close(t.task_id, "planner", "accepted")
    return t.task_id


def run_escalated(reg, dids) -> str:
    t = reg.create_task("endless dispute")
    reg.claim(t.task_id, dids["W1"])
    reg.submit(t.task_id, dids["W1"], "r1")
    reg.validate_result(t.task_id, dids["V1"], accepted=False)   # loop 1
    reg.claim(t.task_id, dids["W1"])
    reg.submit(t.task_id, dids["W1"], "r2")
    reg.validate_result(t.task_id, dids["V1"], accepted=False)   # loop 2
    reg.close(t.task_id, "planner", "cancelled")                 # operator takeover
    return t.task_id


def run_contended(reg, dids) -> str:
    t = reg.create_task("shared db work", db_ref="prod-db")
    reg.claim(t.task_id, dids["W2"])
    reg.submit(t.task_id, dids["W2"], "r1")
    reg.validate_result(t.task_id, dids["V1"], accepted=True)
    reg.close(t.task_id, "planner", "accepted")
    return t.task_id


def run_deep_dispute(reg, dids) -> str:
    """HELD-OUT: 3 rework loops then a NON-cancelled close — the default
    planner (hard_stop=3) misses the escalation. This is the proxy-gain
    detector: a candidate tuned only on the public set never sees it."""
    t = reg.create_task("buried dispute")
    for i in range(4):
        reg.claim(t.task_id, dids["W1"])
        reg.submit(t.task_id, dids["W1"], f"r{i}")
        if i < 3:
            reg.validate_result(t.task_id, dids["V1"], accepted=False)
    reg.validate_result(t.task_id, dids["V1"], accepted=True)
    reg.close(t.task_id, "planner", "accepted")   # should have been escalated
    return t.task_id


def harvest_traces() -> tuple[list[TaskTrace], list[TaskTrace]]:
    reg, dids = fresh_registry()
    public = [
        trace_from_history("green_path", reg.engine.chains[run_green_path(reg, dids)].history()),
        trace_from_history("one_rework", reg.engine.chains[run_one_rework(reg, dids)].history()),
        trace_from_history("escalated", reg.engine.chains[run_escalated(reg, dids)].history()),
        trace_from_history("contended", reg.engine.chains[run_contended(reg, dids)].history()),
    ]
    held_out = [
        trace_from_history("deep_dispute", reg.engine.chains[run_deep_dispute(reg, dids)].history()),
    ]
    return public, held_out


# ---------------------------------------------------------------------------
# The evolution run
# ---------------------------------------------------------------------------


def main() -> None:
    public, held_out = harvest_traces()
    print(f"traces harvested: public={len(public)} held_out={len(held_out)}")
    for t in public + held_out:
        n_steps = len(t.steps)
        n_rej = sum(1 for s in t.steps if s.action == "validate" and not s.payload.get("accepted", True))
        print(f"  {t.name:14s} steps={n_steps} rejects={n_rej} db_ref={t.db_ref}")

    # sanity: default spec is clean on public, misses the held-out dispute
    eng_probe = ArtifactEvolutionEngine(bench=HeldOutBench(public, held_out), rework_threshold=5)
    inc = eng_probe.lineage[-1].artifact
    pub_scores = eng_probe.bench.evaluate(inc, public)
    ho_scores = eng_probe.bench.evaluate(inc)
    print(f"\nincumbent v{inc.version} hard_stop={inc.rework_hard_stop}: "
          f"public_missed={sum(r['missed_escalations'] for r in pub_scores)} "
          f"held_out_missed={sum(r['missed_escalations'] for r in ho_scores)}")

    # ---- the real run ---------------------------------------------------
    # Post-review (C2): governance is executed — approvals are real Ed25519
    # signatures bound to {candidate content, scorecard fingerprint, bench
    # commitment, engine identity}; two board DIDs sign every step.
    from adl_lite.evolution.approvals import KeyRing, acceptance_domain
    ring = KeyRing()
    for _did in ("did:key:zBoard1", "did:key:zBoard2", "did:key:zOps"):
        ring.generate(_did)
    actor = "did:key:zOps"

    def _sign(eng, candidate, card) -> list:
        domain = acceptance_domain(
            engine_id=eng.engine_id,
            candidate_hash=HeldOutBench.artifact_content_hash(candidate),
            scorecard_hash=eng._scorecard_fingerprint(card),
            bench_commitment=eng.bench.commitment or "")
        return [ring.approve(d, domain) for d in ("did:key:zBoard1", "did:key:zBoard2")]

    eng = ArtifactEvolutionEngine(bench=HeldOutBench(public, held_out),
                                  rework_threshold=5,
                                  key_ring=ring, engine_id="fides-p2-drill")
    generations: list[dict] = []
    accepted = False
    MAX_GEN = 12
    proxy_gain_events = 0

    for gen in range(1, MAX_GEN + 1):
        candidate = eng.propose()
        card = eng.evaluate(candidate)
        pub_miss = sum(r["missed_escalations"] for r in card.public_results)
        pub_clean = card.clean_public
        try:
            route = eng.accept(candidate, card, actor=actor,
                               signatures=_sign(eng, candidate, card))
            outcome = "accepted"
            ho_miss = sum(r["missed_escalations"] for r in card.held_out_results)
        except ValueError:
            outcome = "rejected"
            # bench was reset by the rejection path; re-derive held-out miss
            # from the audit: rejected candidates failed held-out dominance.
            ho_miss = None
            # proxy-gain signature: clean+improving on public, still fails held-out
            if pub_clean and pub_miss == 0:
                proxy_gain_events += 1
        trust = eng.lineage[-1].trust_score
        generations.append({
            "gen": gen,
            "candidate": {
                "version": candidate.version,
                "rework_hard_stop": candidate.rework_hard_stop,
                "w_shared": candidate.w_shared,
                "w_rework": candidate.w_rework,
                "w_claim": candidate.w_claim,
            },
            "public_missed": pub_miss,
            "held_out_missed": ho_miss,
            "route": generations[-1]["route"] if outcome == "rejected" and generations else None,
            "outcome": outcome,
            "trust_after": trust,
        })
        # recover route from audit log (authoritative)
        generations[-1]["route"] = eng.audit_log[-1]["route"]
        generations[-1]["risk"] = eng.audit_log[-1]["risk"]
        print(f"gen {gen:2d} v{candidate.version} "
              f"hard_stop={candidate.rework_hard_stop:2d} "
              f"w_shared={candidate.w_shared:4.1f} w_rework={candidate.w_rework:4.1f} "
              f"| pub_miss={pub_miss} | {outcome:8s} route={generations[-1]['route']:20s} "
              f"trust={trust:.3f}")
        if outcome == "accepted":
            accepted = True
            break

    final = eng.lineage[-1]
    results = {
        "accepted": accepted,
        "generations_run": len(generations),
        "generations_to_accept": next(
            (g["gen"] for g in generations if g["outcome"] == "accepted"), None
        ),
        "proxy_gain_events": proxy_gain_events,
        "final_version": final.artifact.version,
        "final_hard_stop": final.artifact.rework_hard_stop,
        "final_trust": final.trust_score,
        "routing_distribution": _dist(g["route"] for g in generations),
        "outcome_distribution": _dist(g["outcome"] for g in generations),
        "risk_distribution": _dist(g["risk"] for g in generations),
        "lineage_report": eng.report(),
        "audit_log": eng.audit_log,
        "generations": generations,
    }
    OUT.write_text(json.dumps(results, indent=2))
    print(f"\n=== RESULTS ===")
    print(f"accepted: {results['accepted']} in {results['generations_to_accept']} generations")
    print(f"proxy-gain events (public-clean but held-out reject): {proxy_gain_events}")
    print(f"routing: {results['routing_distribution']}")
    print(f"final: v{results['final_version']} hard_stop={results['final_hard_stop']} trust={results['final_trust']}")
    print(f"written -> {OUT}")


def _dist(xs) -> dict[str, int]:
    d: dict[str, int] = {}
    for x in xs:
        d[x] = d.get(x, 0) + 1
    return d


if __name__ == "__main__":
    main()
