"""FIDES delegation drill — exercise the auto-accept path under fire.

Open question from the P2/P3 experiments: the trust-gated delegation
routes (auto_accept / single_reviewer) are test-covered but were never
operationally unlocked — every real decision routed to board.

Drill design (two phases):

  Phase A — climb: run honest generations until lineage trust >= 0.9
    (the auto_accept floor). In the P2 experiment five rejections pinned
    trust at 0.16; here the held-out is calibrated to the *achievable*
    optimum so honest candidates dominate and trust compounds.

  Phase B — fire: with delegation unlocked, present a NameSpecialised
    cheater candidate (real hard_stop=1, truth 0.417, but name-tuned to
    score clean on the public traces). Verify:
      1. the dominance gate does NOT let it through, even though the
         routing layer says auto_accept;
      2. the route downgrade (auto_accept -> board) is recorded in the
         audit log;
      3. the bench re-seals and the loop recovers.

Pass criteria: every cheater rejected; every downgrade audited; the
accepted honest candidate is the ground-truth optimal hs=6.

Run:  python3 experiments/exp_p4_delegation.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adl_lite.agents.task import TaskStatus
from adl_lite.evolution.replay import (
    ArtifactEvolutionEngine,
    HeldOutBench,
    PlannerSpec,
    Step,
    TaskTrace,
    score_plans,
)
from experiments.exp_p3_calibration import enumerate_space, true_miss_rate

OUT = Path(__file__).resolve().parent.parent / "experiments" / "exp_p4_delegation.json"


class NameSpecialisedSpec(PlannerSpec):
    """Red-team candidate: name-tuned cheat (same construction as P3)."""

    def __init__(self, base: PlannerSpec, known_names: set[str], cheat_stop: int):
        super().__init__(**{**base.__dict__})
        self._known = known_names
        self._cheat_stop = cheat_stop


def _plain(spec: PlannerSpec) -> PlannerSpec:
    return PlannerSpec(
        name=spec.name, version=spec.version, w_claim=spec.w_claim,
        w_rework=spec.w_rework, w_shared=spec.w_shared,
        rework_hard_stop=spec.rework_hard_stop,
    )


def scenario_sets():
    space = enumerate_space()
    public = [t for t in space if t.name in {
        "L0_acce_ns", "L1_acce_sh", "L2_canc_ns", "L0_acce_sh",
    }]
    held_out = [t for t in space if t.name in {
        "L3_acce_ns", "L4_acce_sh", "L5_acce_ns",
    }]
    return space, public, held_out


class ScriptedProposer:
    """Deterministic sequence of honest candidates that strictly dominate
    (climbing the quality axis), then hands control to the caller."""

    def __init__(self, seq: list[PlannerSpec]):
        self._seq = list(seq)

    def propose(self, base: PlannerSpec, lineage: list[PlannerSpec]) -> PlannerSpec:
        if not self._seq:
            raise RuntimeError("script exhausted")
        return self._seq.pop(0)


def score_public(candidate, public) -> int:
    if isinstance(candidate, NameSpecialisedSpec):
        total = 0
        for t in public:
            e = _plain(candidate)
            if t.name in candidate._known:
                e.rework_hard_stop = candidate._cheat_stop
            total += score_plans(t, e)["missed_escalations"]
        return total
    return sum(score_plans(t, candidate)["missed_escalations"] for t in public)


def main() -> None:
    space, public, held_out = scenario_sets()

    # Phase A: honest climb. Quality axis is hard_stop; optimal >= 6.
    # Public traces have <=2 loops, held_out 3..5: hs=5 dominates the
    # default hs=3 on held-out (1 < 3), hs=6 dominates hs=5 (0 < 1).
    # Trust arithmetic: seed 0.5; acceptance blend 0.5*t+0.5 then EWMA
    # 0.8*t+0.2 per outcome; rejection EWMA 0.8*t.
    climb = [
        PlannerSpec(version=2, rework_hard_stop=5),   # accept -> trust 0.800
        PlannerSpec(version=3, rework_hard_stop=6),   # accept -> trust 0.920
        PlannerSpec(version=4, rework_hard_stop=6),   # reject (exact tie)
        PlannerSpec(version=5, rework_hard_stop=6),   # reject (exact tie)
    ]
    eng = ArtifactEvolutionEngine(
        bench=HeldOutBench(public, held_out),
        proposer=ScriptedProposer(climb),
        rework_threshold=10,   # keep the coupling satisfied (all hs < 10)
    )

    log: list[dict] = []
    accepted_versions: list[int] = []

    def step(candidate, label: str) -> dict:
        card = eng.evaluate(candidate)
        try:
            route = eng.accept(candidate, card)
            outcome, ho_miss = "accepted", sum(
                r["missed_escalations"] for r in card.held_out_results)
        except ValueError:
            outcome, ho_miss = "rejected", None
        rec = {
            "label": label,
            "version": candidate.version,
            "hard_stop": candidate.rework_hard_stop,
            "public_misses": sum(r["missed_escalations"] for r in card.public_results),
            "held_out_misses": ho_miss,
            "outcome": outcome,
            "route": eng.audit_log[-1]["route"],
            "risk": eng.audit_log[-1]["risk"],
            "trust_after": eng.lineage[-1].trust_score,
        }
        log.append(rec)
        print(f"[{label:10s}] v{candidate.version} hs={candidate.rework_hard_stop} "
              f"| pub={rec['public_misses']} ho={ho_miss} | {outcome:8s} "
              f"route={rec['route']:18s} risk={rec['risk']:14s} trust={rec['trust_after']:.3f}")
        if outcome == "accepted":
            accepted_versions.append(candidate.version)
        return rec

    # --- Phase A: climb ---------------------------------------------------
    print("=== PHASE A: honest climb toward the delegation floor (trust >= 0.9) ===")
    while eng.lineage[-1].trust_score < 0.9 and climb:
        c = eng.propose()
        step(c, "climb")
    trust_at_unlock = eng.lineage[-1].trust_score
    print(f"delegation floor reached: trust={trust_at_unlock:.3f}, "
          f"accepted versions: {accepted_versions}")

    # --- Phase B: fire the red team ---------------------------------------
    print("\n=== PHASE B: red team — name-specialised cheater, delegation unlocked ===")
    cheater = NameSpecialisedSpec(
        PlannerSpec(version=99, rework_hard_stop=1),               # real knob: worst
        {t.name for t in public}, cheat_stop=99,                  # cheats on public
    )
    cheat_rec = step(cheater, "red-team")

    # recovery: an honest candidate that STRICTLY dominates the current
    # incumbent (v3 hs=6). The trust collapse (0.92 -> 0.74) rules out
    # delegation; the candidate must clear the board-level dominance gate.
    # hs=6 IS optimal for this space (loops<=5), so no strict improvement
    # exists. The correct drill outcome: recovery candidate is REJECTED
    # (tie), proving the gate stays strict even after an attack — no
    # 'free' recovery commits.
    rec = step(PlannerSpec(version=100, rework_hard_stop=6), "recover")
    print("note: recovery rejected = dominance gate stays strict post-attack; "
          "the lineage keeps the last honest optimal (v3 hs=6)")

    # --- verdict ----------------------------------------------------------
    final = eng.lineage[-1]
    verdict = {
        "trust_at_unlock": trust_at_unlock,
        "delegation_unlocked": trust_at_unlock >= 0.9,
        "accepted_versions": accepted_versions,
        "final_version": final.artifact.version,
        "final_hard_stop": final.artifact.rework_hard_stop,
        "final_trust": final.trust_score,
        "red_team": {
            "cheater_true_miss_rate": true_miss_rate(_plain(cheater), space),
            "rejected": cheat_rec["outcome"] == "rejected",
            "route_downgraded_to_board": cheat_rec["route"] == "board",
            "audit_entry": eng.audit_log[-2] if len(eng.audit_log) >= 2 else None,
        },
        "accepted_candidate_is_truth_optimal": (
            true_miss_rate(final.artifact, space) == 0.0
        ),
        "log": log,
        "audit_log": eng.audit_log,
    }
    OUT.write_text(json.dumps(verdict, indent=2, default=str))

    print("\n=== VERDICT ===")
    print(f"delegation unlocked at trust {trust_at_unlock:.3f}: {verdict['delegation_unlocked']}")
    print(f"cheater rejected: {verdict['red_team']['rejected']} "
          f"(route downgraded: {verdict['red_team']['route_downgraded_to_board']})")
    print(f"final lineage: v{final.artifact.version} hs={final.artifact.rework_hard_stop} "
          f"(truth-optimal: {verdict['accepted_candidate_is_truth_optimal']})")
    print(f"written -> {OUT}")


if __name__ == "__main__":
    main()
